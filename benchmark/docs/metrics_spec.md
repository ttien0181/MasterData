# Metrics specification

Version: `pair-metrics-v1`, `cluster-metrics-v1`, `runtime-metrics-v1`.

## Evaluation scopes

- `U`: the legal pair universe from the dataset task and source-pair policy.
- `L`: supplied pair IDs with known positive or negative labels. `UNKNOWN` rows and legal pairs absent from the label file are outside metric denominators.
- `G+` / `G-`: positive / negative labels inside the explicitly declared gold scope.
- `C`: a unique candidate set checked against record IDs and the legal universe.
- `P`: predicted positives among `L`; for the baseline runner this means `AUTO_MATCH`.
- Metrics over sampled labels describe the sampled labeled set. They are not estimates of population performance without a documented sampling design.

Every metric result has `AVAILABLE`, `NOT_APPLICABLE`, or `BLOCKED` status. Undefined denominators return a null value and a reason; they are never replaced with zero. Counts with an empty class remain valid zero counts.

## Pair classification and ranking metrics

All confusion counts use `L` only. Unknown labels are excluded, and no true negatives are inferred from `U`.

| Metric | Definition and numerator | Denominator | Evaluation universe | Gold requirement | Undefined policy |
| --- | --- | --- | --- | --- | --- |
| TP | Count of `P ∩ G+` | Count | `L` | Known pair labels | Zero is available |
| FP | Count of `P ∩ G-` | Count | `L` | Known pair labels | Zero is available |
| FN | Count of `G+ \ P` | Count | `L` | Known pair labels | Zero is available |
| TN | Count of `G- \ P` | Count | `L` | Known pair labels | Zero is available; no Cartesian inference |
| Precision | `TP` | `TP + FP` | `L` | Known pair labels | N/A when there are no predicted positives |
| Recall | `TP` | `TP + FN` | `L` | Known pair labels | N/A when there is no positive gold |
| F1 | `2 TP` | `2 TP + FP + FN` | `L` | Known pair labels | N/A when denominator is zero; 0 when positives exist but all are missed |
| False merge share | `FP` | `TP + FP` | `L` | Known pair labels | N/A when no positives are predicted |
| False positive rate | `FP` | `FP + TN` | `L` | Known negative pair labels | N/A when there is no negative gold |
| False non-match rate | `FN` | `TP + FN` | `L` | Known positive pair labels | N/A when there is no positive gold |
| Average precision (PR-AUC summary) | Sum over distinct score thresholds of `precision_at_threshold × positive_recall_increment` | Total positive labels | `L` with one comparable finite score per known label | Known positive and negative labels and complete score coverage in `L` | N/A without positives or full score coverage; ties enter together |
| Brier score | Sum `(probability - binary_label)^2` | Number of known scored labels | `L` | Known labels, typed `Probability` for each scored label, and explicit declaration that the probabilities are calibrated; calibration claim needs representative sampling | N/A for `Similarity`/log-odds, unverified calibration, missing scores, or no known labels |
| Review rate | Number of `REVIEW` decisions | Number of known labeled pairs | `L` | Known pair labels | N/A when `L` is empty |
| Auto-decision rate | Number of `AUTO_MATCH` or `NO_MATCH` decisions | Number of known labeled pairs | `L` | Known pair labels | N/A when `L` is empty |
| Auto-match precision | Positive `AUTO_MATCH` count | All `AUTO_MATCH` decisions | `L` | Known labels for auto-matched pairs | N/A when no automatic matches |
| Auto-match true-match recall | Positive `AUTO_MATCH` count | All positive labels | `L` | Known positive pair labels | N/A when no positive gold |
| Coverage at target precision | Count above an already frozen score threshold, if observed labeled-set precision meets target | Number of known labeled pairs | `L` | Threshold selected on an independent dev set; test labels only verify achieved precision | BLOCKED without dev-frozen threshold; N/A if no selected pair or target is not met |

`AUTO_MATCH` is the only positive decision for operational auto-match metrics. `REVIEW` is not silently treated as a positive prediction. A missing prediction row is an implicit negative only for binary pair classification; it is not counted as an auto or review decision.

Current MasterOne coverage is `sampled_labeled_pairs`; all 1,200 labels are on `test`, and history of prior test use is unknown. The current test command is descriptive. No threshold is selected or adjusted from its results. B0 and B1 scores are `Similarity`, so Brier score is N/A. Even a `Probability` score is insufficient for Brier unless the caller explicitly certifies calibration. Calibration and coverage-at-target-precision claims are blocked until representative dev data and a frozen threshold exist.

## Blocking metrics

| Metric | Definition and numerator | Denominator | Evaluation universe | Gold requirement | Undefined policy |
| --- | --- | --- | --- | --- | --- |
| Blocking recall | `|C ∩ G+|` | `|G+|` | Positive labels in supplied gold scope | Complete positive gold for full blocking recall; sampled positives produce the `sampled_labeled_positive_candidate_recall` metric | N/A when no positive labels |
| Reduction ratio | `1 - |C|` | `|U|` | Complete legal pair universe `U` | No gold labels required; requires unique, legal candidates | N/A for empty `U` |
| Candidate count | `|C|` | None | Legal candidates | No gold labels required | Zero available |
| Candidate degree / record | Candidate incidences per record; report mean, p50, p95, and maximum | Record count for mean/quantiles | All dataset records, including degree zero | No gold labels required | N/A when no records |

Candidate duplicates, unknown endpoints, illegal source combinations, or counts above `|U|` fail validation. The present B0/B1 pair runner does not implement a candidate generator; blocking metrics are available as a validated evaluator API and are not presented as baseline results.

## Cluster metrics and validation contract

Cluster membership must contain exactly one non-empty predicted cluster ID for each expected record. Missing or extra records fail closed. Entity metrics require complete, independently verified entity gold. Cluster statistics use contingency counts and `n(n-1)/2` arithmetic; they do not construct transitive pair closure.

| Metric | Definition and numerator | Denominator | Evaluation universe | Gold requirement | Undefined policy |
| --- | --- | --- | --- | --- | --- |
| Pairwise cluster TP | Sum `choose(n_pred_cluster,gold_entity, 2)` | Count | All records in the declared cluster scope | Complete verified entity gold | N/A gate if gold missing; count can be zero |
| Pairwise cluster precision | Pairwise TP | Predicted co-cluster pairs | All records | Complete verified entity gold | N/A if no predicted co-cluster pair |
| Pairwise cluster recall | Pairwise TP | Gold co-entity pairs | All records | Complete verified entity gold | N/A if no gold co-entity pair |
| Pairwise cluster F1 | `2 × precision × recall` | Precision + recall | All records | Complete verified entity gold | N/A if there are no gold or predicted co-cluster pairs |
| B³ precision | Per record: overlap with its gold entity | Predicted cluster size; average over all records | All records including singletons; within-source and cross-source records alike | Complete verified entity gold | N/A if record scope is empty |
| B³ recall | Per record: overlap with its predicted cluster | Gold entity size; average over all records | Same | Complete verified entity gold | N/A if record scope is empty |
| B³ F1 | Harmonic mean of B³ precision and recall | Sum of the two values | Same | Complete verified entity gold | N/A if both are zero |
| Micro purity | Sum of largest predicted-cluster/gold-entity intersection | Record count | All records including singleton clusters | Complete verified entity gold | N/A when empty |
| Over-merge rate | Predicted multi-record clusters containing more than one gold entity | Predicted multi-record clusters | All predicted clusters | Complete verified entity gold | N/A when no predicted multi-record cluster |
| Under-merge rate | Gold multi-record entities split across more than one predicted cluster | Gold multi-record entities | All gold entities | Complete verified entity gold | N/A when no gold multi-record entity |
| Validation coverage | Checked predicted multi-record clusters | All predicted multi-record clusters | Predicted clusters | Explicit validation evidence naming all checked clusters | N/A when there are no multi-record clusters |
| Unsafe cluster rate | Verified-contradiction predicted multi-record clusters | Predicted multi-record clusters | Predicted clusters | Every predicted multi-record cluster validated; contradiction evidence must be independently sourced | BLOCKED under partial validation; N/A with no multi-record clusters or contradiction evidence |

Singleton clusters stay in B³ and purity; they have no pairwise co-cluster pairs. `ClusterValidationEvidence` requires checked IDs to be a subset of all predicted cluster IDs and contradictions to be among checked IDs. Pairwise metrics, impurity, cannot-link input violations, and checker-discovered contradictions are separate concepts; partial checking never implies a safe cluster result.

## Runtime and resource metrics

| Metric | Definition and numerator | Denominator | Evaluation universe | Gold requirement | Undefined policy |
| --- | --- | --- | --- | --- | --- |
| Stage wall time | Monotonic elapsed seconds around one stage call | None | `load`, `fit`, `normalize`, `index`, `candidate`, `score`, `route`, `cluster`, `evaluate`, `report` | None | Unrun/uninstrumented stage is `NOT_RUN_OR_NOT_INSTRUMENTED`, not zero |
| Stage CPU time | Process CPU seconds around that stage call | None | Same stage scope | None | Same |
| End-to-end time | Wall/CPU time around the documented complete operation | None | Supported CLI boundary is adapter load through evaluation; terminal formatting is excluded | None | N/A until its boundary is measured |
| Peak RSS | Best-effort process peak resident bytes; current implementation uses optional psutil or platform `resource` fallback | None | Current process; child processes are not included | None | N/A when unavailable; method/platform is reported |
| Throughput | Completed item count | Stage wall seconds | Named stage only | None | N/A when count absent or elapsed time is zero |

Stage timings are non-overlapping calls; end-to-end is a separate inclusive measurement and is not added to stage totals. The supported pair CLI measures adapter load, score, route, evaluation, post-load end-to-end time, per-stage CPU/wall time, and best-effort process RSS. Terminal report formatting is outside the measured boundary. Fit/normalize/index/candidate/cluster/report are not run by this pair track. Child-process RSS, GPU resources, cold/warm cache splits, API costs, and latency distributions remain unsupported.

## Hand-calculated fixture evidence

`tests/test_g2.py` fixes expected values independently: four pair labels give perfect counts `(2,0,0,2)`, all-wrong counts `(0,2,2,0)`, and no-positive-prediction counts `(0,0,2,2)`; average precision is `5/6`; Brier score is `0.1`; two candidates from a six-pair dedupe universe give reduction ratio `2/3`; a split 2+2 clustering has pair precision `1`, recall `1/2`, F1 `2/3`, B³ precision `1`, recall `3/4`; merging those entities gives pair precision `1/3`, recall `1`, F1 `1/2`, B³ precision `1/2`, recall `1`, and micro purity `1/2`.
