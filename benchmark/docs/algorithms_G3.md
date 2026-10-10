# G3 Pair Algorithms: implementation contract

## Normalization

`standalone/algorithms/normalization.py` normalizes Unicode with NFKC/NFC, case folds and collapses whitespace. Vietnamese accentless name values are optional comparison features (`đ` becomes `d`); the original record is immutable and each normalized field retains its raw value and transform provenance. Phone values are accepted only after conservative domestic-number validation or explicit `+84`/`0084` conversion; invalid values become missing with a quality flag. Email parsing normalizes IDNA domains and case-folds the local part, but leaves dots and plus tags intact for every provider. Address aliases retain all reference candidates and ambiguous results stay ambiguous.

Address reference compatibility is supporting evidence only. It is never a hard conflict. No business timestamp is present in the prediction feature allowlist, so temporal evidence returns `unknown` without actual timestamps and never fabricates a conflict.

## Profiling and blockers

`LabelBlindProfiler` requires `train` or explicit `transductive_unlabeled` scope and rejects dev/test. It computes per-field missingness, cardinality, frequencies, shared values, hubs, exact-block size/skew quantiles, and source distribution.

Blocking operates on `RecordFeatures` plus a source vector. It does not accept IDs or labels. `ExactBlocker`, `MultiRuleUnion`, `CharNgramTfidfTopK`, and `HybridBlocker` return unique, canonical index pairs with sorted reasons. Missing fields emit no block. Sparse inverted postings keep TF-IDF computation sparse; top-k ties are broken by input index. Pair legality follows dedupe, two-source clean-clean, or declared multi-source source-pair policy.

Blocking recall is `|C ∩ G+| / |G+|`; reduction ratio is `1 - |C| / |U|`. These are reportable only with a valid positive gold set and legal pair universe; candidate generation never restores missed candidates from gold.

## Fellegi–Sunter and adjustments

Field comparison levels are exact, fuzzy-high, fuzzy-medium, disagreement, missing, or explicit reference evidence. Valid phone/email disagreement is marked hard conflict; it remains a routing signal and is not converted into a forced negative. Address disagreement is soft. Invalid quality flags suppress affected field evidence under the `quality_aware` policy.

For field `f` and level `l`, smoothed parameters use add-`α` counts:

`m[f,l] = (matches[f,l] + α) / (Σ_l matches[f,l] + αK)`

`u[f,l] = (nonmatches[f,l] + α) / (Σ_l nonmatches[f,l] + αK)`

`FS log score = log(π/(1−π)) + Σ_f log(m[f,l_f]/u[f,l_f])`

All calculations are in log space and probability floors prevent `log(0)`. The typed result is `LogOdds`, not a probability. The deterministic trace records normalized values, level, m/u, contribution, missing/conflict state, quality flags, and reference version. Training accepts TRAIN only; unknown labels are skipped, never used as negatives. Unsupervised EM is not implemented.

Term frequency subtracts `log(value_frequency)` from exact-level evidence. The optional hub policy neutralizes a field contribution once its frequency reaches a declared count threshold. Both are explicit ablation switches, not calibrated probability bonuses. Reference evidence contributes only if its own fitted m/u level is supplied.

## Calibration, routing, Splink, and ablations

Platt calibration uses a damped Newton fit and accepts DEV labels only, requiring both labeled classes. It returns a calibrated `Probability` separately from raw similarity and FS log score. A routing threshold object must identify DEV as its source. AUTO_MATCH uses `score >= auto_match`; NO_MATCH uses `score <= no_match`; the closed interval between them routes to REVIEW. Hard conflicts and insufficient evidence route to REVIEW regardless of score. Current MasterOne test-only coverage cannot fit calibration or select thresholds.

The optional Splink adapter lazily checks the installed distribution, documents required `splink==5.0.0`, invokes configured Linker native prediction, maps native `match_probability` and `match_weight` plus Splink record keys into typed benchmark outputs, and explicitly declares `fixed_pair_scoring=False`. Splink prediction generates candidates from native blocking rules; it is not represented as a scorer over an externally supplied fixed candidate list. Splink is not installed in this environment, so its real package execution smoke is SKIPPED/BLOCKED; the adapter output mapping is exercised with a stub.

| Ablation | Content | Current availability |
|---|---|---|
| A0 | Weighted V1 baseline | available as existing G2 baseline |
| A1 | Fellegi–Sunter | implementation available; dataset fit BLOCKED without train labels |
| A2 | A1 + term frequency | implementation available; no test claim |
| A3 | A2 + hub adjustment | implementation available; threshold is explicit |
| A4 | A3 + quality evidence | implementation available |
| A5 | A4 + Vietnamese normalization | implementation available |
| A6 | A5 + temporal evidence | unavailable until validated timestamp fields exist |

No G3 experiment was run or tuned on test labels.
