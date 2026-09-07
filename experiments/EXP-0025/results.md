# EXP-0025 — Sub-1H vol clustering + fractal (continuation vs failure)

**Generated:** 2026-09-07T04:26:31Z (Modal) · **Verdict: WEAK→lean PROMISING (vol only)**

## Simple answer to the question

Yes — **volatility clustering / expanding tape** is the useful “power-law world” piece for
continuation vs failure. **Sub-1H Hurst fractal** did **not** fire here (not enough clean
5m history in the pre-signal window for H to estimate).

## What showed up

| Signature | n | Hit-1R | vs baseline 3.7% |
|---|---:|---:|---|
| Vol expanding into signal | 88 | **8.0%** | better |
| Alive (cluster + expand) | 41 | **12.2%** | better (thin n) |
| Dead / dying vol | 532 | **2.4%** | worse |
| Highest rv_ratio quartile | 183 | **6.6%** | better |
| Lowest rv_ratio quartile | 183 | **1.6%** | worse |

Alive − dead WR gap ≈ **+9.8pp** (small alive sample).

Soft rank boosts for “alive vol” were only a tiny slot-WR lift (5.6% → 5.9%). Treat as
**diagnostic / soft weight candidate**, not a hard filter.

## Honesty
- Hurst 5m mostly unavailable → ignore “boost_hurst” style claims from the first pass.
- Coverage 730/1400 (52%) — many names lack usable pre-signal 5m.
- Do not auto-wire Peak Hour until a fuller sample + frozen gate.

## Practical tell
Before trusting a 1H continuation: ask if **5m realized vol is expanding into the signal**.
Dying/compressed vol → more failure risk.
