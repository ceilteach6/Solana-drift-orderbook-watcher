# 📓 Projekt jegyzet — feladatmegosztás és védelem-roadmap

> Belső jegyzet. Rögzíti, **mi a felhasználó része** (API-k, linkek, kulcsok) és
> **mi a Claude része** (kód/plumbing), valamint a **védelem (bot-detektálás)
> további felépítési pontjait**. Ha a felhasználó rákérdez, innen tudom a státuszt.

---

## 1. 🔑 A FELHASZNÁLÓ része — API-k, linkek, kulcsok (amit te töltesz ki)

A kód minden külső hozzáférést **env változóból** olvas (lásd `config.example.env`
→ `.env`). A `.env` a `.gitignore` miatt **soha nem kerül a repóba**. A plumbing
kész; csak az értékeket kell beírnod.

| Mit kell megadni | Env kulcs | Hol szerzed be | Állapot |
|---|---|---|---|
| Solana RPC endpoint | `RPC_URL` | Helius (`dev.helius.xyz`), QuickNode, Triton, vagy public mainnet | ⏳ te töltöd |
| (opcionális) read-wallet keypair | `KEYPAIR_PATH` | saját Solana keypair JSON; üresen ephemeral wallet | ⏳ opcionális |
| Telegram értesítés | `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` | @BotFather (token), @userinfobot (chat id) | ⏳ te töltöd |
| Discord értesítés | `ALERT_WEBHOOK_URL` | Discord → Channel → Integrations → Webhooks | ⏳ te töltöd |
| (jövő) DB connection | `DB_URL` | SQLite fájl vagy Postgres URL | 🔲 még nincs kódolva |

**Fontos linkek / végpontok (referencia):**
- Drift docs / DLOB: https://drift.trade , `driftpy` SDK
- Helius RPC: https://dev.helius.xyz
- Telegram Bot API: `https://api.telegram.org/bot<TOKEN>/sendMessage`
- Discord webhook: a csatornából generált URL-re POST `{"content": "..."}`

> Ha új külső API-t/linket akarsz bővíteni: szólj, felveszem ide és bekötöm a
> kódba env-kulcsként — te csak az értéket adod meg.

---

## 2. 🛠️ A CLAUDE része — kód / plumbing (státusz)

| Komponens | Állapot |
|---|---|
| `config/settings.py` — env-alapú konfiguráció | ✅ kész |
| `src/collector/` — L2 modell + driftpy DLOB feed + szintetikus fallback | ✅ kész |
| `src/detector/` — base + repeated_size, layering, flicker, imbalance, spoof_pull | ✅ kész |
| `src/alert/` — dispatcher + console + webhook (Telegram/Discord) | ✅ kész |
| `src/risk_aggregator.py` — EMA per-market risk score + composite threshold | ✅ kész |
| `src/storage/sqlite_store.py` — SQLite perzisztencia (DB_PATH env) + wallet_reputation tábla | ✅ kész |
| `src/reputation/wallet_reputation.py` — EMA reputáció + blocklist (BLOCKLIST_WALLETS env) | ✅ kész (új) |
| `src/watcher.py` — orchestrator (risk + storage + reputation integrálva) | ✅ kész |
| `tests/` — 61/61 teszt átment | ✅ kész |
| **Bug-javítások** | |
| `webhook_alert.py` — HTTP → daemon thread (nem blokkolja az event loop-ot) | ✅ javítva |
| `flicker.py` — bid/ask szétválasztva a presence set-ben (false positive fix) | ✅ javítva |
| `console_alert.py` — hiányzó ikonok hozzáadva (spoof_pull 🎭, risk_aggregator 🔴) | ✅ javítva |
| `drift_client.py` — `inspect.isawaitable()` a `hasattr(__await__)` helyett | ✅ javítva |
| `risk_aggregator.py` — per-tick INFO log eltávolítva (log spam fix) | ✅ javítva |
| `sqlite_store.py` — `_safe_json()` helper: nem-szerializálható details kezelése | ✅ javítva |
| `drift_client.py` — keypair betöltési hiba: try-except + érthetőerror üzenet | ✅ javítva |
| `drift_client.py` — user_map + slot_subscriber teardown a close()-ban | ✅ javítva |

---

## 3. 🛡️ A védelem (bot-detektálás) további felépítési pontjai

A "védelem" itt = a gyanús/bot-szerű orderbook-minták felismerése. Read-only,
nem avatkozik be. Prioritás szerinti felépítés:

### Meglévő detektorok ✅
- `repeated_size` — ismétlődő rendelési méretek (bot-aláírás)
- `layering` — egyoldali fal (layering/spoofing)
- `flicker` — gyors megjelenés/eltűnés (quote stuffing)
- `imbalance` — erős egyoldali nyomás a top szinteken *(új)*

### Következő építési pontok 🔜 (prioritás sorrendben)
1. ~~**Risk-aggregátor**~~ ✅ kész
2. ~~**Spoof-pull detektor**~~ ✅ kész
3. ~~**Time-series tárolás (SQLite)**~~ ✅ kész — `DB_PATH=drift_watcher.db`
4. ~~**Wallet-szintű reputáció / blocklist**~~ ✅ kész — `BLOCKLIST_WALLETS`, `REPUTATION_DECAY`, `REPUTATION_BLOCK_THRESHOLD`; L3 maker adat szükséges a `details["maker"]`-hez
5. **Prometheus metrics exporter** — detekciók/score-ok kitétele scrape-re.
6. **ML-alapú anomáliadetektálás** — a heurisztikák mellé, baseline-tól való
   eltérés alapján.

### Riasztási csatornák
- ✅ console, ✅ JSON, ✅ webhook-plumbing (Telegram/Discord) — **token/URL = user része**
- 🔲 e-mail / PagerDuty — később, ha kell

---

_Frissítés módja: minden új modulnál ezt a fájlt is léptetem (állapot + ki mit ad)._
