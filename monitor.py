#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════╗
║         Lago Lago Ticket Monitor                     ║
║  Checks TicketSwap + lagolago.nl every 15 minutes    ║
║  Logs every run to CSV + sends email alerts          ║
╚══════════════════════════════════════════════════════╝
"""

import csv
import json
import os
import math
import re
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

EMAIL_APP_PASSWORD  = os.getenv("EMAIL_APP_PASSWORD", "")
EMAIL_FROM          = _senv("EMAIL_FROM",  "spjwinter@gmail.com")
EMAIL_TO            = _senv("EMAIL_TO",    "spjwinter@gmail.com")
TELEGRAM_BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID    = os.getenv("TELEGRAM_CHAT_ID",   "").strip()

# Safe env helpers — GitHub Actions sets unset vars to "", not None
def _senv(key: str, default: str) -> str:
    """Return env var or default — handles empty string from unset GitHub vars."""
    v = os.getenv(key, "").strip()
    return v if v else default

def _fenv(key: str, default: float) -> float:
    v = os.getenv(key, "").strip()
    try: return float(v) if v else default
    except ValueError: return default

def _ienv(key: str, default: int) -> int:
    v = os.getenv(key, "").strip()
    try: return int(v) if v else default
    except ValueError: return default

PRICE_STEP         = _ienv("PRICE_STEP",        10)   # alert on every €N drop
PRICE_HIGH_ALERT   = _fenv("PRICE_HIGH_ALERT", 300.0)
LOW_STOCK_ALERT    = _ienv("LOW_STOCK_ALERT",  30)
SEND_STATUS_REPORT = os.getenv("SEND_STATUS_REPORT", "false").strip().lower() == "true"

TICKETSWAP_URL = os.getenv(
    "TICKETSWAP_URL",
    "https://www.ticketswap.nl/event/lago-lago-festival-2025/e0a8e2e0-fc15-4fab-aa77-21f86ff8cffb",
)
LAGOLAGO_URL = "https://lagolago.nl/tickets"
STATE_FILE   = "monitor_state.json"
LOG_FILE     = "price_log.csv"

LOG_HEADERS = [
    "timestamp", "ts_min_price", "ts_max_price", "ts_count",
    "ts_available", "ts_error", "ll_available", "ll_sold_out", "ll_error",
]

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "nl-NL,nl;q=0.9,en-US;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────

def log_run(ts: dict, ll: dict) -> None:
    """Append one row to price_log.csv for every single run."""
    log_path   = Path(LOG_FILE)
    write_header = not log_path.exists()

    row = {
        "timestamp":    datetime.now().isoformat(timespec="seconds"),
        "ts_min_price": ts.get("min_price", ""),
        "ts_max_price": ts.get("max_price", ""),
        "ts_count":     ts.get("count",     ""),
        "ts_available": ts.get("available", ""),
        "ts_error":     ts.get("error",     ""),
        "ll_available": ll.get("available", ""),
        "ll_sold_out":  ll.get("sold_out",  ""),
        "ll_error":     ll.get("error",     ""),
    }

    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=LOG_HEADERS)
        if write_header:
            writer.writeheader()
        writer.writerow(row)

    # Count rows for feedback
    total = sum(1 for _ in open(LOG_FILE, encoding="utf-8")) - 1  # subtract header
    print(f"  📝 Gelogd in {LOG_FILE} (totaal: {total} metingen)")


def read_recent_log(n: int = 8) -> list[dict]:
    """Return the last n rows from price_log.csv."""
    if not Path(LOG_FILE).exists():
        return []
    with open(LOG_FILE, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return rows[-n:]


# ─────────────────────────────────────────────────────────────────────────────
# Notifications
# ─────────────────────────────────────────────────────────────────────────────

def send_email(subject: str, body: str, raise_on_error: bool = False) -> bool:
    """Send email via Gmail SMTP.
    Set raise_on_error=True to surface failures in GitHub Actions logs."""
    if not EMAIL_APP_PASSWORD:
        print(f"  [EMAIL NOT CONFIGURED] {subject}")
        return False
    # Strip spaces — Google shows app passwords as "xxxx xxxx xxxx xxxx"
    password = EMAIL_APP_PASSWORD.replace(" ", "")
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = f"Lago Lago Monitor <{EMAIL_FROM}>"
        msg["To"]      = EMAIL_TO
        msg.attach(MIMEText(body, "plain", "utf-8"))

        with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as s:
            s.ehlo()
            s.starttls()
            s.ehlo()
            s.login(EMAIL_FROM, password)
            s.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())

        print(f"  ✅ Email → {EMAIL_TO}: {subject}")
        return True
    except smtplib.SMTPAuthenticationError as e:
        msg = (
            f"  ❌ Gmail authenticatie mislukt (535):\n"
            f"     Controleer of EMAIL_APP_PASSWORD correct is (zonder spaties).\n"
            f"     Fout: {e}"
        )
        print(msg)
        if raise_on_error:
            raise RuntimeError(msg) from e
        return False
    except Exception as e:
        print(f"  ❌ Email error ({type(e).__name__}): {e}")
        if raise_on_error:
            raise
        return False


def send_telegram(message: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"},
            timeout=10,
        )
        r.raise_for_status()
        return True
    except Exception:
        return False


def notify(subject: str, body: str, raise_on_error: bool = False) -> None:
    send_email(subject, body, raise_on_error=raise_on_error)
    send_telegram(f"<b>{subject}</b>\n\n{body}")


# ─────────────────────────────────────────────────────────────────────────────
# Status report  (sent on demand or when SEND_STATUS_REPORT=true)
# ─────────────────────────────────────────────────────────────────────────────

def send_status_report(ts: dict, ll: dict) -> None:
    now = datetime.now().strftime("%d-%m-%Y %H:%M")

    # ── TicketSwap summary ──
    if ts.get("min_price"):
        ts_line = (
            f"€{ts['min_price']:.2f} – €{ts['max_price']:.2f}  "
            f"({ts.get('count', '?')} tickets beschikbaar)"
        )
    else:
        ts_line = f"Geen data beschikbaar  ({ts.get('error', 'onbekend')})"

    # ── lagolago.nl summary ──
    if ll["available"]:
        ll_line = "✅  Tickets te koop"
    elif ll["sold_out"]:
        ll_line = "❌  Uitverkocht"
    else:
        ll_line = f"–   Niet bepaalbaar  ({ll.get('error', 'onbekend')})"

    # ── Recent history from log ──────────────────────────────────────────────
    recent = read_recent_log(8)
    if recent:
        history_lines = ["Laatste 8 metingen (nieuwste onderaan):"]
        history_lines.append(f"  {'Tijdstip':<20} {'Min prijs':>10} {'Max prijs':>10} {'Aantal':>7}")
        history_lines.append(f"  {'─'*20} {'─'*10} {'─'*10} {'─'*7}")
        for row in recent:
            ts_ts   = row.get("timestamp", "")[:16].replace("T", " ")
            mn      = f"€{float(row['ts_min_price']):.2f}" if row.get("ts_min_price") else "  –"
            mx      = f"€{float(row['ts_max_price']):.2f}" if row.get("ts_max_price") else "  –"
            cnt     = row.get("ts_count", "–")
            history_lines.append(f"  {ts_ts:<20} {mn:>10} {mx:>10} {cnt:>7}")
        history_block = "\n".join(history_lines)
    else:
        history_block = "Nog geen historische data beschikbaar (eerste run)."

    body = (
        f"Huidige situatie op {now}\n"
        f"{'═'*46}\n\n"
        f"TICKETSWAP\n"
        f"  Prijs nu:          {ts_line}\n"
        f"  Alert bij daling:  elke €{PRICE_STEP} stap\n"
        f"  Alert bij stijging: boven €{PRICE_HIGH_ALERT:.0f}\n"
        f"  Alert weinig stock: ≤ {LOW_STOCK_ALERT} tickets\n\n"
        f"LAGOLAGO.NL\n"
        f"  Status:            {ll_line}\n\n"
        f"HISTORIEK\n"
        f"{history_block}\n\n"
        f"LINKS\n"
        f"  TicketSwap:   {TICKETSWAP_URL}\n"
        f"  lagolago.nl:  {LAGOLAGO_URL}\n\n"
        f"{'─'*46}\n"
        f"Lago Lago Monitor · logt elke 15 minuten"
    )
    notify(subject=f"📊 Lago Lago Status — {now}", body=body, raise_on_error=True)


# ─────────────────────────────────────────────────────────────────────────────
# TicketSwap
# ─────────────────────────────────────────────────────────────────────────────

def check_ticketswap() -> dict:
    result = {"available": False, "count": None, "min_price": None, "max_price": None, "error": None}
    try:
        r = requests.get(TICKETSWAP_URL, headers=REQUEST_HEADERS, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")

        for tag in [soup.find("script", {"id": "__NEXT_DATA__"}),
                    *soup.find_all("script", {"type": "application/json"})]:
            if not (tag and tag.string):
                continue
            try:
                data     = json.loads(tag.string)
                listings = _find_listings(data)
                if listings:
                    prices = [p for p in (_extract_price(l) for l in listings) if p]
                    result.update({
                        "available": True, "count": len(listings),
                        "min_price": min(prices) if prices else None,
                        "max_price": max(prices) if prices else None,
                    })
                    return result
            except (json.JSONDecodeError, TypeError):
                pass

        matches = re.findall(r"€\s*(\d{2,3})[.,](\d{2})", r.text)
        if matches:
            prices = [float(f"{e}.{c}") for e, c in matches if 10 < float(f"{e}.{c}") < 2000]
            if prices:
                result.update({"available": True, "min_price": min(prices), "max_price": max(prices)})

        m = re.search(r"(\d+)\s+(?:ticket|kaartj)", r.text, re.IGNORECASE)
        if m:
            result.update({"count": int(m.group(1)), "available": True})

    except requests.Timeout:
        result["error"] = "Timeout"
    except requests.HTTPError as e:
        result["error"] = f"HTTP {e.response.status_code}"
    except Exception as e:
        result["error"] = str(e)
    return result


def _find_listings(obj, depth=0) -> list:
    if depth > 8:
        return []
    if isinstance(obj, dict):
        for key in ("listings", "availableListings", "nodes", "activeListings", "edges"):
            val = obj.get(key)
            if isinstance(val, list) and val and any("price" in str(i).lower() for i in val[:3]):
                return val
        for v in obj.values():
            r = _find_listings(v, depth + 1)
            if r:
                return r
    elif isinstance(obj, list):
        for item in obj:
            r = _find_listings(item, depth + 1)
            if r:
                return r
    return []


def _extract_price(listing: dict) -> float | None:
    if not isinstance(listing, dict):
        return None
    price_obj = listing.get("price", listing)
    if isinstance(price_obj, dict):
        for key in ("totalPriceIncludingServiceFee", "originalPrice", "amount", "value", "price"):
            val = price_obj.get(key) or listing.get(key)
            if val is not None:
                try:
                    f = float(val)
                    return f / 100 if f > 1000 else f
                except (ValueError, TypeError):
                    pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Lago Lago official
# ─────────────────────────────────────────────────────────────────────────────

def check_lagolago_official() -> dict:
    result = {"available": False, "sold_out": False, "error": None}
    SOLD_OUT_KW  = ["uitverkocht", "sold out", "sold-out", "niet meer beschikbaar",
                    "no longer available", "tickets zijn op", "geen tickets"]
    AVAILABLE_KW = ["bestel", "koop nu", "ticket kopen", "buy", "add to cart",
                    "beschikbaar", "in stock", "boek"]
    try:
        r = requests.get(LAGOLAGO_URL, headers=REQUEST_HEADERS, timeout=15)
        r.raise_for_status()
        text_lower = r.text.lower()
        soup = BeautifulSoup(r.text, "lxml")

        for kw in SOLD_OUT_KW:
            if kw in text_lower:
                result["sold_out"] = True
                return result

        for tag in soup.find_all(["a", "button"]):
            if tag.get("disabled") or tag.get("aria-disabled") == "true":
                continue
            if any(kw in tag.get_text(strip=True).lower() for kw in AVAILABLE_KW):
                result["available"] = True
                return result

        for link in soup.find_all("a", href=True):
            if any(k in link["href"].lower() for k in ["checkout", "shop", "bestel", "kopen"]):
                if any(kw in link.get_text(strip=True).lower() for kw in AVAILABLE_KW):
                    result["available"] = True
                    return result

    except requests.Timeout:
        result["error"] = "Timeout"
    except requests.HTTPError as e:
        result["error"] = f"HTTP {e.response.status_code}"
    except Exception as e:
        result["error"] = str(e)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Alert logic
# ─────────────────────────────────────────────────────────────────────────────

def load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)



def _get_band(price: float, step: int) -> int:
    """Return the lower €step boundary the price sits in (e.g. 285 → 280 for step=10)."""
    return int(math.floor(price / step) * step)


def process_and_alert(ts: dict, ll: dict, state: dict) -> dict:
    now       = datetime.now().strftime("%d-%m-%Y %H:%M")
    new_state = state.copy()
    alerts    = 0

    if ll["available"] and not state.get("ll_was_available"):
        notify("🎟️ LAGOLAGO: Officiële tickets beschikbaar!",
               f"Lago Lago verkoopt tickets via hun eigen website!\n\n"
               f"Wees er snel bij:\n{LAGOLAGO_URL}\n\n"
               f"---\n{now}  |  Lago Lago Monitor")
        alerts += 1
    new_state["ll_was_available"] = ll["available"]

    if ll["sold_out"] and not state.get("ll_was_sold_out"):
        notify("😔 LAGOLAGO: Officiële tickets uitverkocht",
               f"lagolago.nl is uitverkocht.\n\nHoud TicketSwap in de gaten:\n{TICKETSWAP_URL}\n\n"
               f"---\n{now}  |  Lago Lago Monitor")
        alerts += 1
    new_state["ll_was_sold_out"] = ll["sold_out"]

    if ts.get("min_price") is not None:
        p = ts["min_price"]

        # ── €10-band tracker: alert on every new lower band ──────────────────
        current_band = _get_band(p, PRICE_STEP)
        prev_band    = state.get("ts_price_band")  # None on first run

        if prev_band is not None and current_band < prev_band:
            # Price crossed one or more €PRICE_STEP boundaries downward
            thresholds = list(range(prev_band, current_band, -PRICE_STEP))
            for threshold in thresholds:
                notify(
                    subject=f"📉 LAGOLAGO: Prijs gezakt onder €{threshold}",
                    body=(
                        f"De laagste prijs op TicketSwap is gezakt onder €{threshold}!\n\n"
                        f"  Laagste prijs nu: €{p:.2f}\n"
                        f"  Grens gepasseerd: €{threshold}\n"
                        f"  Aantal tickets:   {ts.get('count', '?')!s}\n\n"
                        f"Bekijk TicketSwap:\n{TICKETSWAP_URL}\n\n"
                        f"---\n{now}  |  Lago Lago Monitor"
                    ),
                )
                alerts += 1

        new_state["ts_price_band"] = current_band  # always update

        if p > PRICE_HIGH_ALERT and not state.get("ts_price_above"):
            notify(f"📈 LAGOLAGO: Prijs gestegen naar €{p:.2f}",
                   f"Laagste prijs op TicketSwap is boven jouw drempel!\n\n"
                   f"  Laagste prijs:   €{p:.2f}\n"
                   f"  Jouw drempel:    €{PRICE_HIGH_ALERT:.0f}\n\n"
                   f"Bekijk TicketSwap:\n{TICKETSWAP_URL}\n\n"
                   f"---\n{now}  |  Lago Lago Monitor")
            alerts += 1
        new_state["ts_price_above"] = p > PRICE_HIGH_ALERT

    if ts.get("count") is not None:
        low = ts["count"] <= LOW_STOCK_ALERT
        if low and not state.get("ts_low_stock"):
            notify(f"⚠️ LAGOLAGO: Nog maar {ts['count']} tickets op TicketSwap!",
                   f"Het aanbod is bijna op!\n\n"
                   f"  Beschikbaar:   {ts['count']} tickets\n"
                   f"  Jouw drempel:  {LOW_STOCK_ALERT} tickets\n"
                   f"  Laagste prijs: €{ts.get('min_price','?'):.2f}\n\n"
                   f"Bekijk TicketSwap:\n{TICKETSWAP_URL}\n\n"
                   f"---\n{now}  |  Lago Lago Monitor")
            alerts += 1
        new_state["ts_low_stock"] = low

    ts_str = (f"€{ts['min_price']:.2f}–€{ts['max_price']:.2f} ({ts.get('count','?')} tickets)"
              if ts.get("min_price") else f"fout: {ts.get('error','geen data')}")
    ll_str = ("✅ beschikbaar" if ll["available"]
              else ("❌ uitverkocht" if ll["sold_out"] else f"– {ll.get('error','?')}"))
    print(f"  TicketSwap  → {ts_str}")
    print(f"  lagolago.nl → {ll_str}")
    print(f"  Alerts sent → {alerts}")

    new_state.update({"last_check": now, "last_ts_min_price": ts.get("min_price"),
                      "last_ts_count": ts.get("count"), "last_ll_available": ll.get("available")})
    return new_state


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    now = datetime.now().strftime("%d-%m-%Y %H:%M:%S")
    print(f"\n{'─'*52}\n  🎟️  Lago Lago Monitor  ·  {now}\n{'─'*52}")

    state = load_state()
    print(f"  State geladen (laatste check: {state.get('last_check', 'nooit')})\n")

    print("  Checking TicketSwap…")
    ts = check_ticketswap()

    print("  Checking lagolago.nl…")
    ll = check_lagolago_official()

    print()
    log_run(ts, ll)                               # ← always log every run

    new_state = process_and_alert(ts, ll, state)  # ← threshold alerts

    if SEND_STATUS_REPORT:                        # ← on-demand status email
        print("  📬 Status report aangevraagd…")
        send_status_report(ts, ll)

    save_state(new_state)
    print(f"\n  Done ✓\n{'─'*52}\n")


if __name__ == "__main__":
    main()
