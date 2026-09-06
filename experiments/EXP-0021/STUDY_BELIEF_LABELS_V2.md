# EXP-0021 — Belief labels v2 (Modal)

**Generated:** 2026-09-05T22:58:42.393004-04:00
**Runtime:** 88.9s · sample=3353 · labeled_ok=2509 · label_ok_rate=76.8%
**Recommendation:** **HOLD (research only)**

Bakeoff best passer was `skip_digest`, but **user decision 2026-09-05: do not use belief labels in live scoring.** Keep collecting labels for thesis/research as we progress; revisit only with fresh OOS evidence.

## Schema

Orthogonal AI tags: `event_family` × `info_hardness` × `story_phase` × `expectation_gap` (+ rumor/forward/primary/horizon). Derived mode for humans only.

## Counts (labeled admits)

- derived_mode: `{"hype_soft": 986, "quiet": 949, "digest": 634, "continuation_hard": 337, "anticipation": 249, "junk": 113, "other": 79, "stale_narrative": 6}`
- info_hardness: `{"soft_narrative": 1373, "unknown": 1065, "hard_quantified": 915}`
- story_phase: `{"fresh_print": 1312, "quiet": 949, "post_print_digest": 533, "stale_narrative": 415, "pre_event_anticipation": 144}`
- expectation_gap: `{"unknown": 2388, "above_hopes": 622, "below_hopes": 278, "in_line": 65}`

## Strata by derived_mode

| Mode | n | WR | Exp R | med MFE |
|---|---:|---:|---:|---:|
| hype_soft | 986 | 5.9% | 0.264 | 0.012 |
| quiet | 949 | 8.2% | 0.265 | 0.013 |
| digest | 634 | 5.5% | 0.219 | 0.011 |
| continuation_hard | 337 | 4.7% | 0.233 | 0.009 |
| anticipation | 249 | 8.8% | 0.287 | 0.013 |
| junk | 113 | 2.7% | 0.263 | 0.010 |
| other | 79 | 5.1% | 0.251 | 0.014 |
| stale_narrative | 6 | 0.0% | 0.033 | 0.011 |

## Strata by info_hardness

| Hardness | n | WR | Exp R | med MFE |
|---|---:|---:|---:|---:|
| soft_narrative | 1373 | 5.2% | 0.257 | 0.012 |
| unknown | 1065 | 9.4% | 0.277 | 0.013 |
| hard_quantified | 915 | 4.9% | 0.222 | 0.010 |

## Strata by expectation_gap

| Gap | n | WR | Exp R | med MFE |
|---|---:|---:|---:|---:|
| unknown | 2388 | 7.0% | 0.266 | 0.012 |
| above_hopes | 622 | 4.5% | 0.214 | 0.010 |
| below_hopes | 278 | 6.8% | 0.242 | 0.012 |
| in_line | 65 | 3.1% | 0.233 | 0.011 |

## Bakeoff vs v1.1

| Variant | n | WR | Exp R | Capture | PASS? |
|---|---:|---:|---:|---:|---|
| v11 | 720 | 10.4% | 0.297 | 42.8% | — |
| belief_v2_adj | 720 | 9.6% | 0.289 | 40.6% | no |
| skip_digest | 706 | 10.6% | 0.308 | 42.8% | YES |
| skip_hype_soft | 693 | 10.0% | 0.285 | 40.6% | no |
| skip_junk | 719 | 10.3% | 0.288 | 42.8% | no |

## Recommended live action

- **HOLD (research only)** — Live stays on `continuation_score_v1.1`.
- Belief labels / `skip_digest` stay offline for thesis and further study.
- No live rewrite.

## Note on compute

This study ran on **Modal** (parallel `label_one.map`). Local machine only launched the job and wrote results.
