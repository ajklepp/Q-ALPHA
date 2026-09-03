# UTS v2.6 — 1H launch OS (Chat A parity)

**Shipped on main** with this commit.

## Operating rules
| Piece | v2.6 |
|-------|------|
| Buy trigger | Last **completed 1H** bar: buy + launch + red. 3H buy is **not** required. |
| HTF | Daily range ≥25%, close > SMA50, SMA20 rising, price ≥ $5. Pre-filter universe once/session. |
| Hours ET | **07 / 11 / 12 / 13** only. 08:00 not on the list. Last bar = 13:00 close. |
| Scan clock | 07:05, 11:05, 12:05, 13:05 ET. HTF refresh 04:30 (+ noon). |
| Capacity | 2 **per hourly scan**, dynamic **2–10** slots, **no 2/day cap**. |
| Sizing | Slot-then-size: S=$300 frozen until N=10, then S=equity/10. Never full-pool/2. |
| Idle | `idle_no_1r` day ≥6 if never +1R and not trailing. Do **not** cut day 2–5. |

## Keep from 2.5
Kill-until-+1R then BE `entry×0.997`. No ORB. No day-2 99% tighten. SPY context-only. Analog WR not a hard gate. `paper_gate.py` still 20 trades / 45% WR before size-up **beyond** the slot ladder.

## Bar source
Polygon 1H aggregates, start-labeled (`polygon_1h_aggs_start_labeled`). Close hour = start.hour+1.

## Tests
`tests/test_uts_v26_hours_capacity.py` plus updated entry-gate / structure / watch-queue tests.
