# Vendored Track 100 modules (frozen)

This folder is the **only** place EXP-0026 is allowed to read
`paper_filter_winloss_v1` thresholds and `C_ratchet_struct` exit math.

**Do not invent medians or ratchet constants here.**

`paper_filter.py` imports Track 100 `features.py`:

```python
from features import daily_feature_dict, enrich_features_1h, enrich_features_daily, row_to_feature_dict
```

The Modal image mounts `VENDOR.glob('*.py')`. If `features.py` is missing, the
walk-forward scan finishes then dies in `load_filter_module`.

Copy from the Track 100 repo on the laptop:

```powershell
cd C:\Users\ajkle\Documents\Q-ALPHA
git fetch && git checkout cursor/exp0026-vendor-features-fa9c
py -3 experiments\EXP-0026\vendor_from_track100.py
.\venv\Scripts\python.exe -m modal run experiments/EXP-0026/study_php_t100_wf_5k_modal.py
```

Expected sources (first existing wins):

- `TRACK100_ROOT`
- `../track-100` · `../Track 100` · `../Track100`

Required files (copied if present):

- `paper_filter.py` — frozen IS medians, `paper_filter_winloss_v1`
- `paper_exit.py` + `trail_exits.py` — exact `C_ratchet_struct` book
- import closure (Modal path-bug fix): `features.py`, `playbook.py`,
  `wave.py`, `backtest.py`, `leverage.py`
- optional: `walkforward_5k.py`, `ops_stack_5k.py` (patterns only; book lives in Q-ALPHA)

A `MANIFEST.json` with sha256 is written so the study can prove it did not
recut thresholds on OOS.
