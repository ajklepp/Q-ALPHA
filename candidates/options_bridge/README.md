# Options data bridge (BSF Phase 9A)

Thin **read-only** HTTP JSON service so Best Strategy Finder can use Aaron’s
laptop **TWS paper** market data **without** opening `ib_insync` or logging
into IBKR itself.

| | |
|---|---|
| HTTP bind | **127.0.0.1:8787** (loopback only — never `0.0.0.0`) |
| TWS paper | **127.0.0.1:7497** (API must be enabled in TWS) |
| clientId | **71** (dedicated; do not reuse Peak Hour / TSD / BSF 3910x ids) |
| Auth / secrets | none — no IBKR credentials, no cloud Gateway |
| Orders | **none** — `placeOrder` / modify / cancel are not implemented; those paths return 403/405 |

This package does **not** import Peak Hour, TSD trail monitor, PHP scan, or
book-state modules. It is a data layer only.

Smoke-test happens on the laptop after pull (TWS open). CI / cloud VMs must
**not** call `ib.connect`.

## Start / stop (Windows laptop)

From repo root, using the project venv:

```powershell
.\candidates\start_options_bridge.ps1
.\candidates\stop_options_bridge.ps1
```

- Log: `candidates/logs/options_bridge_YYYY-MM-DD.log` (ET date)
- PID: `candidates/logs/options_bridge.pid`
- Python: `venv\Scripts\python.exe candidates\options_bridge\server.py --host 127.0.0.1 --port 8787 --client-id 71`

Env overrides (still validated): `OPTIONS_BRIDGE_PORT`, `OPTIONS_BRIDGE_HOST`
(loopback only), `OPTIONS_BRIDGE_CLIENT_ID` (rejects reserved ids),
`OPTIONS_BRIDGE_TWS_HOST`, `OPTIONS_BRIDGE_TWS_PORT`.

TWS must be logged into **paper**, **Enable ActiveX and Socket Clients** on,
socket port **7497**. Confirm clientId **71** is unused in TWS API settings.

## Envelope

Success:

```json
{"ok": true, "ts_utc": "2026-09-12T23:41:00Z", "data": {}}
```

Error (TWS down → HTTP 503 on data routes; health still 200):

```json
{"ok": false, "error": {"code": "TWS_DISCONNECTED", "message": "TWS paper API is not connected (127.0.0.1:7497)."}}
```

`ts_utc` is ISO-8601 UTC. Health `data` never includes account ids.

## Endpoints

Base: `http://127.0.0.1:8787`

### `GET /v1/health`

Service up. Reports TWS socket state. No secrets.

Query: none.

```bash
curl -sS http://127.0.0.1:8787/v1/health
```

```json
{
  "ok": true,
  "ts_utc": "2026-09-12T23:41:00Z",
  "data": {
    "tws_connected": true,
    "host": "127.0.0.1",
    "port": 7497,
    "readonly": true,
    "accounts_n": 1,
    "client_id": 71
  }
}
```

`host` / `port` are the **TWS** target. `accounts_n` is a count only.

### `GET /v1/underlying/quote?symbol=`

Underlying snapshot for Phase 9A.

| Param | Required | Notes |
|---|---|---|
| `symbol` | yes | e.g. `SPY` |

```bash
curl -sS "http://127.0.0.1:8787/v1/underlying/quote?symbol=SPY"
```

```json
{
  "ok": true,
  "ts_utc": "2026-09-12T23:41:00Z",
  "data": {
    "symbol": "SPY",
    "conId": 756733,
    "bid": 560.10,
    "ask": 560.14,
    "last": 560.12,
    "close": 558.90,
    "mid": 560.12,
    "market_price": 560.12
  }
}
```

Missing IB ticks are JSON `null`. `mid` needs a live bid **and** ask.
`market_price` is last, else mid, else close.

### `GET /v1/options/chain?symbol=`

SecDef chain. `exchanges` are **richest strikes first** (then the rest).

| Param | Required | Notes |
|---|---|---|
| `symbol` | yes | underlying ticker |

```bash
curl -sS "http://127.0.0.1:8787/v1/options/chain?symbol=SPY"
```

```json
{
  "ok": true,
  "ts_utc": "2026-09-12T23:41:00Z",
  "data": {
    "symbol": "SPY",
    "underlying_conId": 756733,
    "exchanges": [
      {
        "exchange": "SMART",
        "tradingClass": "SPY",
        "multiplier": "100",
        "expirations": ["20260918", "20260925"],
        "strikes": [550.0, 555.0, 560.0]
      }
    ]
  }
}
```

### `POST /v1/options/qualify`

Qualify option specs → `conId` / `ok` / `error` per row.

```bash
curl -sS -X POST http://127.0.0.1:8787/v1/options/qualify \
  -H "Content-Type: application/json" \
  -d '{"contracts":[{"symbol":"SPY","expiry":"20260918","strike":555,"right":"P","exchange":"SMART","currency":"USD"}]}'
```

```json
{
  "ok": true,
  "ts_utc": "2026-09-12T23:41:00Z",
  "data": {
    "contracts": [
      {
        "symbol": "SPY",
        "expiry": "20260918",
        "strike": 555,
        "right": "P",
        "exchange": "SMART",
        "currency": "USD",
        "conId": 123456789,
        "ok": true,
        "error": null
      }
    ]
  }
}
```

Body: `{ "contracts": [ { symbol, expiry, strike, right, exchange?, currency? } ] }`  
`expiry` = `YYYYMMDD`. `right` = `C`/`P` (or CALL/PUT). Max 40 rows.

### `GET /v1/options/quote`

One option snapshot. **Either** `conId` **or** `symbol+expiry+strike+right`.

| Param | Required | Notes |
|---|---|---|
| `conId` | one of | IB contract id (alias `conid`) |
| `symbol` | or fields | underlying |
| `expiry` | with symbol | `YYYYMMDD` |
| `strike` | with symbol | number |
| `right` | with symbol | `C` / `P` |
| `exchange` | no | default `SMART` |
| `currency` | no | default `USD` |

```bash
curl -sS "http://127.0.0.1:8787/v1/options/quote?conId=123456789"
curl -sS "http://127.0.0.1:8787/v1/options/quote?symbol=SPY&expiry=20260918&strike=555&right=P"
```

```json
{
  "ok": true,
  "ts_utc": "2026-09-12T23:41:00Z",
  "data": {
    "symbol": "SPY",
    "conId": 123456789,
    "expiry": "20260918",
    "strike": 555.0,
    "right": "P",
    "bid": 1.20,
    "ask": 1.28,
    "last": 1.24,
    "close": 1.10,
    "mid": 1.24
  }
}
```

### `POST /v1/options/quotes` (optional batch)

```bash
curl -sS -X POST http://127.0.0.1:8787/v1/options/quotes \
  -H "Content-Type: application/json" \
  -d '{"conIds":[123456789,123456790]}'
```

```json
{
  "ok": true,
  "ts_utc": "2026-09-12T23:41:00Z",
  "data": {
    "quotes": [
      {"conId": 123456789, "ok": true, "error": null, "bid": 1.20, "ask": 1.28, "last": 1.24, "close": 1.10, "mid": 1.24}
    ]
  }
}
```

Max 20 `conIds`.

### `GET /v1/options/hist`

1-hour MIDPOINT bars (defaults) for a stock or option.

| Param | Required | Default | Notes |
|---|---|---|---|
| `conId` | one of | | contract id |
| `symbol` + option fields | or | | same as quote; stock if no expiry |
| `secType` / `sec_type` | no | inferred | `STK` vs option fields |
| `bar_size` | no | `1 hour` | alias `barSize` |
| `duration` | no | `10 D` | IB duration string |
| `what` | no | `MIDPOINT` | alias `whatToShow` |
| `use_rth` | no | `true` | alias `useRTH` |

```bash
curl -sS --get "http://127.0.0.1:8787/v1/options/hist" \
  --data-urlencode "conId=123456789" \
  --data-urlencode "bar_size=1 hour" \
  --data-urlencode "duration=10 D" \
  --data-urlencode "what=MIDPOINT" \
  --data-urlencode "use_rth=true"
```

```json
{
  "ok": true,
  "ts_utc": "2026-09-12T23:41:00Z",
  "data": {
    "conId": 123456789,
    "symbol": "SPY",
    "bar_size": "1 hour",
    "duration": "10 D",
    "what": "MIDPOINT",
    "use_rth": true,
    "bars": [
      {"ts": "2026-09-11T14:00:00-04:00", "open": 1.10, "high": 1.30, "low": 1.05, "close": 1.22}
    ]
  }
}
```

### `POST /v1/phase9a/put_credit_snapshot`

Pick a put-credit pair in a DTE window.

| Body field | Required | Notes |
|---|---|---|
| `symbol` | yes | underlying |
| `und_px` | no | if omitted, uses `/v1/underlying/quote` mark |
| `dte_min` | yes | inclusive DTE |
| `dte_max` | yes | inclusive DTE |
| `short_moneyness` | yes | `> 0.5` = K/S (e.g. `0.95`); `≤ 0.5` = OTM fraction (`0.05`) |

Short put = nearest listed strike at/below target. Long put = next lower strike.
`credit_mid` = short mid − long mid. `width` = short strike − long strike.

```bash
curl -sS -X POST http://127.0.0.1:8787/v1/phase9a/put_credit_snapshot \
  -H "Content-Type: application/json" \
  -d '{"symbol":"SPY","und_px":560,"dte_min":20,"dte_max":45,"short_moneyness":0.95}'
```

```json
{
  "ok": true,
  "ts_utc": "2026-09-12T23:41:00Z",
  "data": {
    "symbol": "SPY",
    "und_px": 560.0,
    "expiry": "20261016",
    "short": {"conId": 111, "strike": 532.0, "right": "P", "bid": 2.10, "ask": 2.20, "mid": 2.15},
    "long": {"conId": 222, "strike": 527.0, "right": "P", "bid": 1.40, "ask": 1.50, "mid": 1.45},
    "credit_mid": 0.70,
    "width": 5.0
  }
}
```

## Order routes (refused)

Any path containing `order`, `placeOrder`, `cancel`, `modify`, `bracket`,
`whatif`, `trade`, `submit`, `exercise`, `globalcancel` returns **403** (GET)
or **405** (POST/PUT/PATCH/DELETE) with:

```json
{"ok": false, "error": {"code": "ORDER_ROUTE_FORBIDDEN", "message": "Read-only options bridge refuses order route '/v1/order'. No place / modify / cancel is implemented."}}
```

```bash
curl -sS -X POST http://127.0.0.1:8787/v1/order -H "Content-Type: application/json" -d "{}"
```

There is no place / modify / cancel implementation behind any path.

## BSF integration notes

- Call **HTTP only**. Do not import this package’s `ib_session` from BSF if you
  can avoid it — the point is that BSF never opens `ib_insync`.
- On `TWS_DISCONNECTED` (HTTP 503): start TWS paper, enable API 7497, retry.
  The bridge reconnects with an 8s timeout (2 attempts); it will not hang forever.
- Keep clientId **71** exclusive to this process.

## Reserved TWS clientIds (do not use 71 for anything else)

Do **not** configure this bridge as: `1, 5, 75, 76, 85, 86, 88, 89, 93–99`
or BSF `3910x` ranges (`3910–3919`, `39100–39199`). Those belong to Peak Hour,
TSD, flatten, probes, and BSF’s own clients.

## Layout

```
candidates/options_bridge/
  server.py          # stdlib HTTP + Phase 9A router
  ib_session.py      # read-only ib_insync (lazy import)
  config.py          # bind / clientId / timeouts
  README.md
candidates/start_options_bridge.ps1
candidates/stop_options_bridge.ps1
```
