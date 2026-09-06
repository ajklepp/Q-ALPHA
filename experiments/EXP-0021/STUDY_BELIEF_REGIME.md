# EXP-0021 — Belief-regime edge study

**Generated:** 2026-09-05T20:37:00.700587-04:00
**Corpus:** `corpus_htf_universe_social.csv` · slots=2 · OOS≥2026-08-11
**Runtime:** 882.4s
**Recommendation:** **MORE_LABELS**

Rule↔AI agreement below 60% — do **not** trust rule-only bakeoffs. Expand AI labeling before any soft/hard ship.

## Labeling

- Symbol-days cached: 2509
- Admit regime counts: `{"quiet": 1793, "anticipation": 1231, "digest": 187, "reacceleration": 142}`

### AI validation (rule ↔ deep brief)

- n=300 · agreement=57.3% (floor 60%)
- trust_rule_bakeoffs=False
- confusion (rule→ai): `{"digest": {"anticipation": 11, "digest": 5, "reacceleration": 2}, "quiet": {"quiet": 91, "reacceleration": 16, "anticipation": 47, "digest": 11}, "anticipation": {"anticipation": 69, "quiet": 1, "reacceleration": 19, "digest": 4}, "reacceleration": {"reacceleration": 7, "digest": 6, "anticipation": 11}}`

## A) Descriptive strata (hypothesis check)

Hypothesis: digest worst; reacceleration / anticipation better than quiet/digest.

### Admits

| Regime | n | WR | Exp R | med MFE | med day MFE |
|---|---:|---:|---:|---:|---:|
| anticipation | 1231 | 5.4% | 0.222 | 0.010 | 0.010 |
| digest | 187 | 4.3% | 0.249 | 0.012 | 0.012 |
| quiet | 1793 | 7.4% | 0.274 | 0.012 | 0.013 |
| reacceleration | 142 | 6.3% | 0.271 | 0.012 | 0.012 |

### Taken under v1.1

| Regime | n | WR | Exp R | med MFE | med day MFE |
|---|---:|---:|---:|---:|---:|
| anticipation | 257 | 7.0% | 0.223 | 0.013 | 0.013 |
| digest | 51 | 9.8% | 0.382 | 0.022 | 0.022 |
| quiet | 374 | 12.8% | 0.336 | 0.017 | 0.017 |
| reacceleration | 38 | 10.5% | 0.308 | 0.011 | 0.011 |

Notes:
- anticipation not clearly > digest

## B) Bakeoff vs v1.1 (pre-registered PASS gate)

PASS: exp ≥ v1.1 AND capture ≥ v1.1 − 1pp (or clear exp win with capture −2pp).

| Variant | n | WR | Exp R | Capture | OOS exp | PASS? |
|---|---:|---:|---:|---:|---:|---|
| v11 | 720 | 10.4% | 0.297 | 42.8% | 0.357 | — |
| demote_digest | 720 | 10.6% | 0.294 | 43.3% | 0.348 | no |
| boost_reaccel | 720 | 10.1% | 0.289 | 42.8% | 0.357 | no |
| boost_anticip | 720 | 10.7% | 0.273 | 41.7% | 0.320 | no |
| prefer_belief | 720 | 10.6% | 0.263 | 41.1% | 0.303 | no |
| skip_digest | 718 | 10.6% | 0.293 | 42.8% | 0.348 | no |

## Recommended live action

- **MORE_LABELS** — Rule↔AI agreement below 60% — do **not** trust rule-only bakeoffs. Expand AI labeling before any soft/hard ship.
- Provisional (untrusted rules): **every** bakeoff variant also failed the PASS gate vs v1.1 (prefer_belief / boosts / demote / skip all lower or flat exp). So even if agreement were fixed tomorrow, this first rule layer did not find a shippable edge.
- No live rewrite in this study. Decide after reading numbers.

## What the strata suggest (cautious)

- On **admits**, `quiet` has the highest expectancy (0.274); `anticipation` is weakest (0.222) — opposite of a naive “boost excitement” story.
- On **v1.1 taken**, `digest` looks *better* (exp 0.382, n=51) — likely selection (v1.1 already demotes bad tape) or label noise, not a reason to chase digest.
- Main AI disagreement: rules call many names `quiet` while deep briefs call them `anticipation` / `reacceleration` (story present, keywords weak).

## Decision menu

1. HOLD — narrative for thesis only
2. SOFT — demote/boost only
3. HARD — skip digest
4. MORE_LABELS — expand AI labeling before ship
