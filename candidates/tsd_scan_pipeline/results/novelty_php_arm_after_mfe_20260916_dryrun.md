# Novelty — arm-after-MFE trail (vs PR #16)

PR #16 Mode B trailed MAE-p50 **from entry**. Slightly better overall
in some samples, but **lost on rippers** (MFE≥4% of entry). Strategy
Finder: do not prefer that B over live A.

This note is the next design: trail **arms only after +3% / +4% MFE**.
Widths from per-ticker prior MAE–MFE (p50 / lock=70% of p50). Kill
ratchets up with the trail. No hard bank. No live patch.

- asof: 2026-09-16
- verdict: MECHANISM_ONLY
- prefer_b_for_live_later: False
- least-bad B on rippers: B_arm3_lock (mean 4.75%)
- sample: n_bars>=3 (6/6)

Invite human review before any live Peak Hour change.
