#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════╗
║         Lago Lago Ticket Monitor                     ║
║  Checks TicketSwap + lagolago.nl every 15 minutes    ║
║  Sends Telegram alerts when thresholds are hit       ║
╚══════════════════════════════════════════════════════╝

Alerts are sent when:
  • Price drops below PRICE_DROP_ALERT
  • Price rises above PRICE_HIGH_ALERT
  • Fewer than LOW_STOCK_ALERT tickets remain on TicketSwap
  • Official tickets become available on lagolago.nl

State is cached between runs so you only get alerted once
per threshold crossing — not every 15 minutes.
"""

import json
import os
import re
import sys
from datetime import datetime

import requests
from bs4 import BeautifulSoup

# ─────────────────────────────────────────────────────────────────────────────
# Configuration — set these as GitHub Secrets / Variables (see README.md)
# ─────────────────────────────────────────────────────────────────────────────

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

# Price thresholds in euros
PRICE_DROP_ALERT = float(os.getenv("PRICE_DROP_ALERT", "230"))   # Alert when min price drops below this
PRICE_HIGH_ALERT = float(os.getenv("PRICE_HIGH_ALERT", "400"))   # Alert when min price rises above this
LOW_STOCK_ALERT  = int(os.getenv("LOW_STOCK_ALERT", "30"))        # Alert when ≤ this many tickets left

# ⚠️  Update TICKETSWAP_URL to the Lago Lago 2026 event page.
#     Find it by going to ticketswap.nl and searching for "Lago Lago 2026".
#     The URL looks like: ticketswap.nl/event/lago-lago-2026/<uuid>
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
    "Accept-Language": "nl-NL,nl;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
}


# ─────────────────────────────────────────────────────────────────────────────
# Telegram
# ─────────────────────────────────────────────────────────────────────────────

def send_telegram(message: str) -> bool:
    """Send a Telegram message. Returns True on success."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"  [NO TELEGRAM] {message[:100]}")
        return False

    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            },
            timeout=10,
        )
        r.raise_for_status()
        print(f"  ✅ Telegram alert sent")
        return True
    except Exception as e:
        print(f"  ❌ Telegram error: {e}")
        return False


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
    """
    Scrapes the TicketSwap event page.
    Returns: { available, count, min_price, max_price, error }
    """
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

        # ── Strategy 1: Next.js __NEXT_DATA__ JSON ──────────────────────────
        script = soup.find("script", {"id": "__NEXT_DATA__"})
        if script and script.string:
            try:
                data = json.loads(script.string)
                listings = _find_listings_in_next_data(data)
                if listings:
                    prices = [_extract_price(l) for l in listings]
                    prices = [p for p in prices if p is not None]
                    result.update({
                        "available": True,
                        "count": len(listings),
                        "min_price": min(prices) if prices else None,
                        "max_price": max(prices) if prices else None,
                    })
                    return result
            except (json.JSONDecodeError, TypeError, AttributeError):
                pass  # Fall through to next strategy

        # ── Strategy 2: Embedded JSON-LD or data attributes ─────────────────
        for script in soup.find_all("script", {"type": "application/json"}):
            try:
                data = json.loads(script.string or "")
                listings = _find_listings_in_next_data(data)
                if listings:
                    prices = [_extract_price(l) for l in listings]
                    prices = [p for p in prices if p is not None]
                    result.update({
                        "available": True,
                        "count": len(listings),
                        "min_price": min(prices) if prices else None,
                        "max_price": max(prices) if prices else None,
                    })
                    return result
            except (json.JSONDecodeError, TypeError):
                pass

        # ── Strategy 3: Regex price extraction from page text ───────────────
        price_matches = re.findall(r"€\s*(\d{2,3})[.,](\d{2})", r.text)
        if price_matches:
            prices = [float(f"{euros}.{cents}") for euros, cents in price_matches]
            prices = [p for p in prices if 10 < p < 1000]  # Sanity filter
            if prices:
                result.update({
                    "available": True,
                    "min_price": min(prices),
                    "max_price": max(prices),
                })

        # ── Strategy 4: Count from page text ────────────────────────────────
        count_match = re.search(r"(\d+)\s+(?:ticket|kaartj)", r.text, re.IGNORECASE)
        if count_match:
            result["count"] = int(count_match.group(1))
            result["available"] = True

    except requests.Timeout:
        result["error"] = "Timeout (>15s)"
    except requests.HTTPError as e:
        result["error"] = f"HTTP {e.response.status_code}"
    except Exception as e:
        result["error"] = str(e)

    return result


def _find_listings_in_next_data(obj, depth: int = 0) -> list:
    """Recursively search Next.js data for ticket listing arrays."""
    if depth > 8:
        return []

    if isinstance(obj, dict):
        # Common keys used by TicketSwap
        for key in ("listings", "availableListings", "nodes", "activeListings", "edges"):
            val = obj.get(key)
            if isinstance(val, list) and len(val) > 0:
                # Make sure these look like ticket listings (have price info)
                if any("price" in str(item).lower() for item in val[:3]):
                    return val

        for v in obj.values():
            result = _find_listings_in_next_data(v, depth + 1)
            if result:
                return result

    elif isinstance(obj, list):
        for item in obj:
            result = _find_listings_in_next_data(item, depth + 1)
            if result:
                return result

    return []


def _extract_price(listing: dict) -> float | None:
    """Extract a euro price from a listing dict."""
    if not isinstance(listing, dict):
        return None

    # Try nested: listing.price.totalPriceIncludingServiceFee etc.
    price_obj = listing.get("price", listing)
    if isinstance(price_obj, dict):
        for key in (
            "totalPriceIncludingServiceFee",
            "originalPrice",
            "amount",
            "value",
            "price",
        ):
            val = price_obj.get(key) or listing.get(key)
            if val is not None:
                try:
                    f = float(val)
                    # TicketSwap sometimes returns cents (e.g. 25750 = €257.50)
                    return f / 100 if f > 1000 else f
                except (ValueError, TypeError):
                    pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Lago Lago official site checker
# ─────────────────────────────────────────────────────────────────────────────

def check_lagolago_official() -> dict:
    """
    Checks lagolago.nl/tickets for official ticket availability.
    Returns: { available, sold_out, error }
    """
    result = {"available": False, "sold_out": False, "error": None}

    SOLD_OUT_KEYWORDS = [
        "uitverkocht", "sold out", "sold-out",
        "niet meer beschikbaar", "no longer available",
        "tickets zijn op", "geen tickets",
    ]
    AVAILABLE_KEYWORDS = [
        "bestel", "koop nu", "ticket kopen", "buy", "add to cart",
        "beschikbaar", "in stock", "boek", "shop",
    ]

    try:
        r = requests.get(LAGOLAGO_URL, headers=REQUEST_HEADERS, timeout=15)
        r.raise_for_status()
        text_lower = r.text.lower()
        soup = BeautifulSoup(r.text, "lxml")

        # Check sold-out first
        for kw in SOLD_OUT_KEYWORDS:
            if kw in text_lower:
                result["sold_out"] = True
                return result

        # Look for active buy buttons (not disabled)
        for tag in soup.find_all(["a", "button"]):
            if tag.get("disabled") or tag.get("aria-disabled") == "true":
                continue
            tag_text = tag.get_text(strip=True).lower()
            if any(kw in tag_text for kw in AVAILABLE_KEYWORDS):
                result["available"] = True
                return result

        # Check for checkout/shop links in hrefs
        for link in soup.find_all("a", href=True):
            href = link["href"].lower()
            link_text = link.get_text(strip=True).lower()
            if any(k in href for k in ["checkout", "shop", "bestel", "kopen"]):
                if any(kw in link_text for kw in AVAILABLE_KEYWORDS):
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

def process_and_alert(ts: dict, ll: dict, state: dict) -> dict:
    """
    Compares current results to previous state.
    Only sends alerts when something changes (no spam).
    Returns updated state.
    """
    now = datetime.now().strftime("%d-%m-%Y %H:%M")
    new_state = state.copy()
    alerts_sent = 0

    # ── Official tickets ────────────────────────────────────────────────────
    if ll["available"] and not state.get("ll_was_available"):
        send_telegram(
            f"🎟️ <b>OFFICIËLE TICKETS BESCHIKBAAR!</b>\n\n"
            f"Lago Lago verkoopt weer tickets via hun eigen website.\n"
            f"Wees er snel bij!\n\n"
            f"👉 <a href='{LAGOLAGO_URL}'>Koop nu op lagolago.nl</a>\n\n"
            f"<i>🕐 {now} · Lago Lago Monitor</i>"
        )
        alerts_sent += 1
    new_state["ll_was_available"] = ll["available"]

    if ll["sold_out"] and not state.get("ll_was_sold_out"):
        send_telegram(
            f"😔 <b>Officiële tickets uitverkocht</b>\n\n"
            f"lagolago.nl geeft aan dat tickets uitverkocht zijn.\n"
            f"Houd TicketSwap in de gaten voor doorverkoop.\n\n"
            f"👉 <a href='{TICKETSWAP_URL}'>Bekijk TicketSwap</a>\n\n"
            f"<i>🕐 {now} · Lago Lago Monitor</i>"
        )
        alerts_sent += 1
    new_state["ll_was_sold_out"] = ll["sold_out"]

    # ── TicketSwap price ─────────────────────────────────────────────────────
    if ts.get("min_price") is not None:
        p = ts["min_price"]

        price_below = p < PRICE_DROP_ALERT
        if price_below and not state.get("ts_price_below"):
            send_telegram(
                f"📉 <b>Prijs gedaald onder jouw drempel!</b>\n\n"
                f"Laagste prijs: <b>€{p:.2f}</b>\n"
                f"Jouw drempel: €{PRICE_DROP_ALERT:.0f}\n"
                f"Tickets beschikbaar: {ts.get('count', '?')}\n\n"
                f"👉 <a href='{TICKETSWAP_URL}'>Bekijk op TicketSwap</a>\n\n"
                f"<i>🕐 {now} · Lago Lago Monitor</i>"
            )
            alerts_sent += 1
        new_state["ts_price_below"] = price_below

        price_above = p > PRICE_HIGH_ALERT
        if price_above and not state.get("ts_price_above"):
            send_telegram(
                f"📈 <b>Prijs gestegen boven drempel!</b>\n\n"
                f"Laagste prijs: <b>€{p:.2f}</b>\n"
                f"Jouw drempel: €{PRICE_HIGH_ALERT:.0f}\n\n"
                f"👉 <a href='{TICKETSWAP_URL}'>Bekijk op TicketSwap</a>\n\n"
                f"<i>🕐 {now} · Lago Lago Monitor</i>"
            )
            alerts_sent += 1
        new_state["ts_price_above"] = price_above

    # ── TicketSwap stock ─────────────────────────────────────────────────────
    if ts.get("count") is not None:
        count = ts["count"]
        low_stock = count <= LOW_STOCK_ALERT
        if low_stock and not state.get("ts_low_stock"):
            send_telegram(
                f"⚠️ <b>Nog maar {count} tickets op TicketSwap!</b>\n\n"
                f"Het aanbod is bijna op (drempel: ≤{LOW_STOCK_ALERT}).\n"
                f"Laagste prijs: €{ts.get('min_price', '?'):.2f}\n\n"
                f"👉 <a href='{TICKETSWAP_URL}'>Bekijk op TicketSwap</a>\n\n"
                f"<i>🕐 {now} · Lago Lago Monitor</i>"
            )
            alerts_sent += 1
        new_state["ts_low_stock"] = low_stock

    # ── Status log ──────────────────────────────────────────────────────────
    ts_status = (
        f"€{ts.get('min_price', '?'):.2f}–€{ts.get('max_price', '?'):.2f} "
        f"({ts.get('count', '?')} tickets)"
        if ts.get("min_price")
        else f"error: {ts.get('error', 'no data')}"
    )
    ll_status = (
        "✅ beschikbaar"
        if ll["available"]
        else ("❌ uitverkocht" if ll["sold_out"] else f"— ({ll.get('error', 'no data')})")
    )
    print(f"  TicketSwap  → {ts_status}")
    print(f"  lagolago.nl → {ll_status}")
    print(f"  Alerts sent → {alerts_sent}")

    new_state["last_check"] = now
    new_state["last_ts_min_price"] = ts.get("min_price")
    new_state["last_ts_count"] = ts.get("count")
    new_state["last_ll_available"] = ll.get("available")
    return new_state


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    now = datetime.now().strftime("%d-%m-%Y %H:%M:%S")
    print(f"\n{'─' * 52}")
    print(f"  🎟️  Lago Lago Monitor  ·  {now}")
    print(f"{'─' * 52}")

    state = load_state()
    print(f"  State loaded (last check: {state.get('last_check', 'never')})\n")

    print("  Checking TicketSwap…")
    ts = check_ticketswap()

    print("  Checking lagolago.nl…")
    ll = check_lagolago_official()

    print()
    new_state = process_and_alert(ts, ll, state)
    save_state(new_state)

    print(f"\n  Done ✓")
    print(f"{'─' * 52}\n")


if __name__ == "__main__":
    main()
