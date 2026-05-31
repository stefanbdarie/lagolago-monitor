#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════╗
║         Lago Lago Ticket Monitor                     ║
║  Checks TicketSwap + lagolago.nl every 15 minutes    ║
║  Sends EMAIL alerts when thresholds are hit          ║
╚══════════════════════════════════════════════════════╝

E-mail alerts are sent when:
  • Price drops below PRICE_DROP_ALERT  (default €250)
  • Price rises above PRICE_HIGH_ALERT  (default €300)
  • Fewer than LOW_STOCK_ALERT tickets remain on TicketSwap (default 30)
  • Official tickets become available / sell out on lagolago.nl
"""

import json
import os
import re
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests
from bs4 import BeautifulSoup

# ─────────────────────────────────────────────────────────────────────────────
# Configuration  (override via GitHub Secrets / Variables)
# ─────────────────────────────────────────────────────────────────────────────

# Email — set EMAIL_APP_PASSWORD as a GitHub Secret
EMAIL_FROM        = os.getenv("EMAIL_FROM",         "spjwinter@gmail.com")
EMAIL_TO          = os.getenv("EMAIL_TO",           "spjwinter@gmail.com")
EMAIL_APP_PASSWORD = os.getenv("EMAIL_APP_PASSWORD", "")  # Gmail App Password

# Telegram (optional, leave empty to disable)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID",   "")

# Alert thresholds
PRICE_DROP_ALERT = float(os.getenv("PRICE_DROP_ALERT", "250"))  # € alert below this
PRICE_HIGH_ALERT = float(os.getenv("PRICE_HIGH_ALERT", "300"))  # € alert above this
LOW_STOCK_ALERT  = int(os.getenv("LOW_STOCK_ALERT",    "30"))   # tickets alert below this

# URLs
TICKETSWAP_URL = os.getenv(
    "TICKETSWAP_URL",
    "https://www.ticketswap.nl/event/lago-lago-festival-2025/e0a8e2e0-fc15-4fab-aa77-21f86ff8cffb",
)
LAGOLAGO_URL = "https://lagolago.nl/tickets"
STATE_FILE   = "monitor_state.json"

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "nl-NL,nl;q=0.9,en-US;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


# ─────────────────────────────────────────────────────────────────────────────
# Notifications
# ─────────────────────────────────────────────────────────────────────────────

def send_email(subject: str, body_text: str) -> bool:
    """Send a plain-text email via Gmail SMTP."""
    if not EMAIL_APP_PASSWORD:
        print(f"  [EMAIL NOT CONFIGURED] {subject}")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = f"Lago Lago Monitor <{EMAIL_FROM}>"
        msg["To"]      = EMAIL_TO
        msg.attach(MIMEText(body_text, "plain", "utf-8"))

        with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as server:
            server.starttls()
            server.login(EMAIL_FROM, EMAIL_APP_PASSWORD)
            server.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())

        print(f"  ✅ Email verzonden → {EMAIL_TO}: {subject}")
        return True
    except Exception as e:
        print(f"  ❌ Email error: {e}")
        return False


def send_telegram(message: str) -> bool:
    """Send a Telegram message (optional, only if configured)."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
                "parse_mode": "HTML",
            },
            timeout=10,
        )
        r.raise_for_status()
        print("  ✅ Telegram alert verzonden")
        return True
    except Exception as e:
        print(f"  ❌ Telegram error: {e}")
        return False


def notify(subject: str, body: str) -> None:
    """Send alert via email (+ Telegram if configured)."""
    send_email(subject, body)
    send_telegram(f"<b>{subject}</b>\n\n{body}")


# ─────────────────────────────────────────────────────────────────────────────
# State management
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


# ─────────────────────────────────────────────────────────────────────────────
# TicketSwap checker
# ─────────────────────────────────────────────────────────────────────────────

def check_ticketswap() -> dict:
    result = {
        "available": False,
        "count": None,
        "min_price": None,
        "max_price": None,
        "error": None,
    }

    try:
        r = requests.get(TICKETSWAP_URL, headers=REQUEST_HEADERS, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")

        # Strategy 1: Next.js __NEXT_DATA__ JSON
        script = soup.find("script", {"id": "__NEXT_DATA__"})
        if script and script.string:
            try:
                data = json.loads(script.string)
                listings = _find_listings(data)
                if listings:
                    prices = [p for p in (_extract_price(l) for l in listings) if p]
                    result.update({
                        "available": True,
                        "count":     len(listings),
                        "min_price": min(prices) if prices else None,
                        "max_price": max(prices) if prices else None,
                    })
                    return result
            except (json.JSONDecodeError, TypeError):
                pass

        # Strategy 2: application/json script tags
        for tag in soup.find_all("script", {"type": "application/json"}):
            try:
                data = json.loads(tag.string or "")
                listings = _find_listings(data)
                if listings:
                    prices = [p for p in (_extract_price(l) for l in listings) if p]
                    result.update({
                        "available": True,
                        "count":     len(listings),
                        "min_price": min(prices) if prices else None,
                        "max_price": max(prices) if prices else None,
                    })
                    return result
            except (json.JSONDecodeError, TypeError):
                pass

        # Strategy 3: regex price extraction
        matches = re.findall(r"€\s*(\d{2,3})[.,](\d{2})", r.text)
        if matches:
            prices = [float(f"{e}.{c}") for e, c in matches if 10 < float(f"{e}.{c}") < 2000]
            if prices:
                result.update({
                    "available": True,
                    "min_price": min(prices),
                    "max_price": max(prices),
                })

        count_m = re.search(r"(\d+)\s+(?:ticket|kaartj)", r.text, re.IGNORECASE)
        if count_m:
            result["count"]     = int(count_m.group(1))
            result["available"] = True

    except requests.Timeout:
        result["error"] = "Timeout"
    except requests.HTTPError as e:
        result["error"] = f"HTTP {e.response.status_code}"
    except Exception as e:
        result["error"] = str(e)

    return result


def _find_listings(obj, depth: int = 0) -> list:
    if depth > 8:
        return []
    if isinstance(obj, dict):
        for key in ("listings", "availableListings", "nodes", "activeListings", "edges"):
            val = obj.get(key)
            if isinstance(val, list) and len(val) > 0:
                if any("price" in str(item).lower() for item in val[:3]):
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
# Lago Lago official site checker
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
            href = link["href"].lower()
            text = link.get_text(strip=True).lower()
            if any(k in href for k in ["checkout", "shop", "bestel", "kopen"]):
                if any(kw in text for kw in AVAILABLE_KW):
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
# Alert logic  (only fires once per threshold crossing)
# ─────────────────────────────────────────────────────────────────────────────

def process_and_alert(ts: dict, ll: dict, state: dict) -> dict:
    now       = datetime.now().strftime("%d-%m-%Y %H:%M")
    new_state = state.copy()
    alerts    = 0

    # ── Official tickets became available ───────────────────────────────────
    if ll["available"] and not state.get("ll_was_available"):
        notify(
            subject="🎟️ LAGOLAGO: Officiële tickets beschikbaar!",
            body=(
                f"Lago Lago verkoopt weer tickets via hun eigen website!\n\n"
                f"Wees er snel bij — ze gaan hard.\n\n"
                f"Koop nu: {LAGOLAGO_URL}\n\n"
                f"---\nTijd: {now}  |  Lago Lago Monitor"
            ),
        )
        alerts += 1
    new_state["ll_was_available"] = ll["available"]

    # ── Official tickets sold out ────────────────────────────────────────────
    if ll["sold_out"] and not state.get("ll_was_sold_out"):
        notify(
            subject="😔 LAGOLAGO: Officiële tickets uitverkocht",
            body=(
                f"De officiële tickets op lagolago.nl zijn uitverkocht.\n\n"
                f"Houd TicketSwap in de gaten voor doorverkoop:\n"
                f"{TICKETSWAP_URL}\n\n"
                f"---\nTijd: {now}  |  Lago Lago Monitor"
            ),
        )
        alerts += 1
    new_state["ll_was_sold_out"] = ll["sold_out"]

    # ── Price dropped below threshold ────────────────────────────────────────
    if ts.get("min_price") is not None:
        p = ts["min_price"]

        if p < PRICE_DROP_ALERT and not state.get("ts_price_below"):
            notify(
                subject=f"📉 LAGOLAGO: Prijs gezakt naar €{p:.2f}",
                body=(
                    f"De laagste ticketprijs op TicketSwap is gezakt!\n\n"
                    f"  Laagste prijs:   €{p:.2f}\n"
                    f"  Hoogste prijs:   €{ts.get('max_price', '?'):.2f}\n"
                    f"  Beschikbaar:     {ts.get('count', '?')} tickets\n"
                    f"  Jouw drempel:    €{PRICE_DROP_ALERT:.0f}\n\n"
                    f"Bekijk TicketSwap:\n{TICKETSWAP_URL}\n\n"
                    f"---\nTijd: {now}  |  Lago Lago Monitor"
                ),
            )
            alerts += 1
        new_state["ts_price_below"] = p < PRICE_DROP_ALERT

        # ── Price rose above threshold ───────────────────────────────────────
        if p > PRICE_HIGH_ALERT and not state.get("ts_price_above"):
            notify(
                subject=f"📈 LAGOLAGO: Prijs gestegen naar €{p:.2f}",
                body=(
                    f"De laagste ticketprijs op TicketSwap is boven jouw drempel!\n\n"
                    f"  Laagste prijs:   €{p:.2f}\n"
                    f"  Jouw drempel:    €{PRICE_HIGH_ALERT:.0f}\n\n"
                    f"Bekijk TicketSwap:\n{TICKETSWAP_URL}\n\n"
                    f"---\nTijd: {now}  |  Lago Lago Monitor"
                ),
            )
            alerts += 1
        new_state["ts_price_above"] = p > PRICE_HIGH_ALERT

    # ── Low stock ────────────────────────────────────────────────────────────
    if ts.get("count") is not None:
        count     = ts["count"]
        low_stock = count <= LOW_STOCK_ALERT

        if low_stock and not state.get("ts_low_stock"):
            notify(
                subject=f"⚠️ LAGOLAGO: Nog maar {count} tickets op TicketSwap!",
                body=(
                    f"Het aanbod op TicketSwap is bijna op!\n\n"
                    f"  Beschikbaar:   {count} tickets\n"
                    f"  Jouw drempel:  {LOW_STOCK_ALERT} tickets\n"
                    f"  Laagste prijs: €{ts.get('min_price', '?'):.2f}\n\n"
                    f"Bekijk TicketSwap:\n{TICKETSWAP_URL}\n\n"
                    f"---\nTijd: {now}  |  Lago Lago Monitor"
                ),
            )
            alerts += 1
        new_state["ts_low_stock"] = low_stock

    # ── Status log ──────────────────────────────────────────────────────────
    ts_str = (
        f"€{ts['min_price']:.2f}–€{ts['max_price']:.2f} ({ts.get('count','?')} tickets)"
        if ts.get("min_price") else f"fout: {ts.get('error','geen data')}"
    )
    ll_str = (
        "✅ beschikbaar" if ll["available"]
        else ("❌ uitverkocht" if ll["sold_out"] else f"— {ll.get('error','geen data')}")
    )
    print(f"  TicketSwap  → {ts_str}")
    print(f"  lagolago.nl → {ll_str}")
    print(f"  Alerts sent → {alerts}")

    new_state.update({"last_check": now, "last_ts_min_price": ts.get("min_price"),
                      "last_ts_count": ts.get("count"), "last_ll_available": ll.get("available")})
    return new_state


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
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
    new_state = process_and_alert(ts, ll, state)
    save_state(new_state)
    print(f"\n  Done ✓\n{'─'*52}\n")


if __name__ == "__main__":
    main()
