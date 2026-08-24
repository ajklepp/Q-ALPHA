"""
strategy_lab/AT1_BASELINE.md — regression policy (Option A, 2026-08-23)

Authoritative replay for 2026-08-21 is the **tip** entry/settle path
(sequential per-ticker settle, scan-merge candidate order).

Recorded tip result:
  order: USDE → ABUS → JOBY
  Pool A end: $2966.42
  Pool B end: $2969.14
  ABUS shares: 62 (sized after USDE kill off ~$2970 pool)

Pre-split 1a6c9da used a different candidate order (ABUS → JOBY → USDE) and
therefore ABUS=63 / A=$2966.47 / B=$2969.27. That difference is **order under
sequential compounding**, not a strategy-logic bend. Option A accepts tip order
as the AT1 baseline going forward.

Evidence artifact (untracked OK): results/at1_baseline_1a6c9da_forward_2026-08-21.json
"""
