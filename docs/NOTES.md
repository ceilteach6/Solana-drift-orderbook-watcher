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
| `src/detector/` — base + repeated_size, layering, flicker | ✅ kész |
| `src/detector/imbalance.py` — orderbook-imbalance detektor | ✅ kész |
| `src/alert/` — dispatcher + console + webhook (Telegram/Discord) csonk | ✅ kész |
| `src/risk/aggregator.py` — risk-aggregátor (EMA + hiszterézis + cooldown) | ✅ kész |
| `src/selftest.py` — algoritmikus önteszt (`--selftest`) + élő health-check | ✅ kész |
| `src/storage/sqlite_store.py` — time-series tárolás (SQLite) + `--dbstats` | ✅ kész |
| `src/dashboard/` — TradingView Lightweight Charts dashboard (`--dashboard`) | ✅ kész |
| `src/replay/` — replay/backtesting + paraméter-sweep (`--replay`) | ✅ kész (új) |
| `src/watcher.py` — orchestrator | ✅ kész |
| `examples/quickstart.py`, `tests/` | ✅ kész |

---

## 3. 🛡️ A védelem (bot-detektálás) további felépítési pontjai

A "védelem" itt = a gyanús/bot-szerű orderbook-minták felismerése. Read-only,
nem avatkozik be. Prioritás szerinti felépítés:

### Meglévő detektorok ✅
- `repeated_size` — ismétlődő rendelési méretek (bot-aláírás)
- `layering` — egyoldali fal (layering/spoofing)
- `flicker` — gyors megjelenés/eltűnés (quote stuffing)
- `imbalance` — erős egyoldali nyomás a top szinteken
- `spoof_pull` — nagy fal visszahúzása ár-elmozdulással korrelálva (spoofing)

### Aggregáció ✅
- **Risk-aggregátor** (`src/risk/aggregator.py`) — a detektorok score-jait
  piaconként egy simított kockázati szintté (noisy-OR + EMA) vonja össze, és
  hiszterézissel + cooldownnal csak tartósan magas szintnél riaszt. Kapcsolható
  (`RISK_AGGREGATION`); kikapcsolva a régi per-detekció mód fut. *(kész)*

### Tárolás ✅
- **Time-series tárolás (SQLite)** (`src/storage/sqlite_store.py`) — detekciók +
  risk-score (és opcionálisan a teljes L2 könyv) perzisztálása. `STORAGE_ENABLED`,
  `DB_PATH`, `PERSIST_SNAPSHOTS`. Visszanézés: `python main.py --dbstats`. Ez az
  alap a replay-hez, elemzéshez és a TradingView-stílusú dashboardhoz. *(kész)*

### Vizualizáció ✅
- **Dashboard** (`src/dashboard/`) — stdlib HTTP szerver + TradingView
  Lightweight Charts frontend. Ár + detekció-markerek + risk-panel, a SQLite-ból
  olvasva (WAL → a watcher közben ír). `python main.py --dashboard`. *(kész)*

### Replay / backtesting ✅
- **Replay-motor** (`src/replay/engine.py`) — a `PERSIST_SNAPSHOTS=true` mellett
  rögzített L2 könyvek offline újrajátszása a valódi detektor-stacken + risk-
  aggregátoron, a tárolt timestampekkel (az időablakos detektorok pontosan úgy
  viselkednek, mint élőben). `python main.py --replay [--market M] [--since W]
  [--until W]`. **Küszöbhangolás**: `--sweep PARAM=v1,v2,...` — bármely numerikus
  setting végigpróbálása ugyanazon a rögzített adaton, összehasonlító táblával.
  *(kész)*

### Következő építési pontok 🔜 (prioritás sorrendben)
1. **Multi-venue collectorok (egész Solana orderbook)** — lásd lent.
2. **Prometheus metrics exporter** — detekciók/score-ok kitétele scrape-re.
3. **Wallet-szintű reputáció / blocklist** — ismétlődő gyanús makerek jelölése.
   (Adatforrás-link → user része.)
4. **ML-alapú anomáliadetektálás** — a heurisztikák mellé, baseline-tól való
   eltérés alapján (a perzisztált idősoron tanítva).

---

## 4. 🌐 Kiterjesztés az egész Solana hálózatra (architektúra-jegyzet)

A kérdés: bővíthető-e a projekt az egész Solana hálózatra? Két értelemben:

**A) Több Solana-tőzsde (orderbook-szint) — KÖNNYEN.** A detektorok, a
risk-aggregátor, a tárolás, a dashboard és az alert **mind az absztrakt
`OrderbookSnapshot`-on dolgoznak** — nem tudnak Driftről. Csak a
`src/collector/` Drift-specifikus. Új venue (Phoenix, OpenBook, Zeta, Mango) =
**új collector**, ami ugyanabba a pipeline-ba táplál. Kis-közepes munka, mert az
absztrakció már megvan. Ez a `MARKETS` és a collector-réteg bővítése.

**B) Teljes lánc / minden tranzakció — ÚJ ÁG.** Ez már nem orderbook-
mikrostruktúra, hanem általános on-chain analitika (Geyser/tranzakció-stream,
Helius webhookok). Más adatforrás + **új detektor-család** kell (wash trading,
sandwich/MEV, rug, token-launch). A meglévő detektorok erre nem alkalmazhatók,
de a risk/tárolás/alert/dashboard mag újrahasználható egy testvér-projektként.

### Riasztási csatornák
- ✅ console, ✅ JSON, ✅ webhook-plumbing (Telegram/Discord) — **token/URL = user része**
- 🔲 e-mail / PagerDuty — később, ha kell

---

_Frissítés módja: minden új modulnál ezt a fájlt is léptetem (állapot + ki mit ad)._
