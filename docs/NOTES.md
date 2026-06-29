# Project notes & task split

## What Claude builds vs. what you provide

| Piece | Who | Where |
|---|---|---|
| Detection logic, alerting plumbing, feed abstraction | Claude | `src/` |
| Solana RPC endpoint | You | `.env` → `RPC_URL` |
| Telegram bot token + chat id | You | `.env` → `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` |
| Discord / generic webhook URL | You | `.env` → `ALERT_WEBHOOK_URL` |
| Optional signing keypair | You | `.env` → `KEYPAIR_PATH` (not needed for read-only) |

---

## Setting up webhook alerts

### Telegram
1. Message [@BotFather](https://t.me/BotFather) → `/newbot` → copy the **token**
2. Message [@userinfobot](https://t.me/userinfobot) → copy your **chat id**
3. Set in `.env`:
   ```
   TELEGRAM_BOT_TOKEN=<token>
   TELEGRAM_CHAT_ID=<chat_id>
   ```

### Discord
1. Server Settings → Integrations → Webhooks → New Webhook → copy URL
2. Set in `.env`:
   ```
   ALERT_WEBHOOK_URL=<webhook_url>
   ```

### Generic JSON webhook
Any endpoint that accepts `POST` with `{"content": "..."}` works.

---

## Defense / detection roadmap

- [x] Repeated-size detector
- [x] Layering / spoofing-like wall detector
- [x] Order-flicker detector
- [x] Orderbook-imbalance detector
- [ ] Time-of-day / session baseline (anomaly relative to normal volume)
- [ ] Cross-market correlation (bot moves SOL and BTC simultaneously)
- [ ] ML-based anomaly scorer (isolation forest on snapshot features)
- [ ] Wallet-level reputation / blocklist (track addresses behind flagged orders)
- [ ] Prometheus metrics endpoint (for Grafana dashboards)
- [ ] SQLite / Postgres time-series storage + replay mode
