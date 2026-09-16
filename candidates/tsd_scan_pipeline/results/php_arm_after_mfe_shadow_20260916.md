# Peak Hour paper shadow — B_arm4_lock

**PAPER ONLY. Does NOT change live trails, keep-profit, kill, or entries.**

- Mode: `B_arm4_lock` — arm +4.00% of entry, lock width 70% of MAE-p50 (floor 1.00% off high)
- As-of: `20260916`  updated `2026-09-15T21:41:12.804564-04:00`
- Live exits changed: **False**
- Snapshots this file: 1 (paper open 1 / paper closed 0)
- Would-have-exited while live still open: 0
- Cost assumed on paper close: 0.0015 of entry notional

Disable: `PHP_ARM_AFTER_MFE_SHADOW=0` (see SHADOW.md / REVERT.md).

| Symbol | Live T1 bank | Live rem | Paper armed | Paper MFE | Lock width | Paper stop | Paper |
|--------|-------------:|---------:|:-----------:|----------:|-----------:|-----------:|-------|
| DEMO | yes | 5 | yes | 4.50% | 1.54% | 10.28907 | OPEN |

## Notes

- Language is **% of entry** / **% off high**. Not R-multiples.
- Widths come from the ticker profile MAE-p50 (prior analogs), not the live path.
- Live A still banks T1 at ~+2% of entry. Paper B does not.
