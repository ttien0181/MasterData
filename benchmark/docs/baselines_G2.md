# G2 baseline contracts

These deterministic baselines are benchmark-owned implementations. They import no application modules and consume only two business-feature `Record`s at scoring time. Pair IDs are attached after scoring by the runner, outside the baseline feature calculation. Pair labels and record/pair annotations are used only by the evaluator.

## B0 exact/rule baseline

Version: `B0-exact-rule-v1`.

- Normalize comparison strings with Unicode NFKC, casefold, and whitespace collapse.
- Convert phone values to digits; only equal non-empty values with at least seven digits count as exact phone evidence.
- Return `AUTO_MATCH` when any one of these conditions holds: exact non-empty email; exact phone evidence; or both exact non-empty name and exact non-empty address.
- Otherwise return `NO_MATCH`.
- Score is typed `Similarity(1)` when a rule fires and `Similarity(0)` otherwise. It is not a probability.
- Evidence lists each rule that fired (`email_exact`, `phone_exact_digits_ge_7`, `name_exact`, `address_exact`). Output is deterministic.

## B1 Weighted V1 reimplementation

Version: `Weighted V1 reimplementation v1.0`.

| Field | Weight | Similarity |
| --- | ---: | --- |
| name | 0.35 | Dice coefficient over character trigrams of NFKC, casefolded, whitespace-collapsed text with two boundary spaces |
| phone | 0.25 | Exact equality after retaining digits only |
| email | 0.25 | Exact equality after NFKC and casefold |
| address | 0.15 | Dice coefficient over character trigrams of normalized text |

When either value for a field is missing, that field is omitted and remaining weights are renormalized to sum to one. If all four fields are missing, score is `Similarity(0)` and decision is `REVIEW`.

- `AUTO_MATCH` at weighted similarity `>= 0.82`.
- `NO_MATCH` at weighted similarity `<= 0.35`.
- `REVIEW` between those thresholds.
- Score type is `Similarity`; no calibrated probability or log-likelihood is claimed.
- Weights, normalization rules, and thresholds are fixed in this version. They were not fit or selected on the current test labels. No train/dev data exists, so any tuning or target-precision threshold selection remains blocked.

This implementation is explicitly a **Weighted V1 reimplementation**. It does not wrap or import the legacy application pipeline; any later comparison to legacy behavior requires a separate fixture-based compatibility review.
