# 🎟️ Lago Lago Ticket Monitor

Checks TicketSwap and lagolago.nl every 15 minutes and sends a **Telegram message** when:

- 📉 Price drops below your threshold
- 📈 Price rises above your threshold
- ⚠️ Fewer than 30 tickets remain on TicketSwap
- 🎟️ Official tickets become available on lagolago.nl

---

## Setup in 3 steps

### Step 1 — Create a Telegram bot (5 minutes)

1. Open Telegram and search for **@BotFather**
2. Send `/newbot` and follow the prompts → copy the **Bot Token** (looks like `123456:ABC-DEF...`)
3. Start a chat with your new bot (search by its username)
4. To get your **Chat ID**: search for **@userinfobot** on Telegram, press Start → it shows your ID

### Step 2 — Add GitHub Secrets

In your GitHub repo: **Settings → Secrets and variables → Actions → New repository secret**

| Secret name          | Value                     |
|----------------------|---------------------------|
| `TELEGRAM_BOT_TOKEN` | Your bot token from Step 1|
| `TELEGRAM_CHAT_ID`   | Your chat ID from Step 1  |

### Step 3 — Update the TicketSwap URL (optional)

The monitor defaults to the Lago Lago 2025 event URL. To use the 2026 URL:

1. Go to [ticketswap.nl](https://www.ticketswap.nl) and search for "Lago Lago 2026"
2. Copy the full event URL
3. In GitHub: **Settings → Secrets and variables → Actions → Variables tab**
4. Create variable `TICKETSWAP_URL` with the 2026 event URL

---

## Customize thresholds (optional)

Add these as **GitHub Variables** (not secrets — they're not sensitive):

| Variable          | Default | Description                              |
|-------------------|---------|------------------------------------------|
| `PRICE_DROP_ALERT`| `230`   | Alert when min price drops below this (€)|
| `PRICE_HIGH_ALERT`| `400`   | Alert when min price rises above this (€)|
| `LOW_STOCK_ALERT` | `30`    | Alert when fewer than this many tickets  |

---

## Schedule

Runs at **:07, :22, :37 and :52** past every hour — that's every 15 minutes, 96 times per day.
Well within GitHub Actions' free tier (2,000 min/month free; each run uses ~1–2 minutes).

---

## Troubleshooting

**No Telegram messages?** Check that the bot token and chat ID are correct. Make sure you started a chat with the bot first.

**TicketSwap shows no price data?** TicketSwap is a JavaScript-heavy app. If the requests-based scraper doesn't extract data, the [Playwright branch](https://playwright.dev/python/) can be used instead — open an issue.

**How do I check if it's running?** Go to your repo → **Actions tab** → you'll see a run every 15 minutes.
