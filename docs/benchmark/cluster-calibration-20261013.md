# Experimental synthetic score sweep, 2026-10-13

This experiment reports the observed precision/recall curve and statistical support. It selects no production threshold or acceptable risk target, and changes no runtime configuration. The supplied data is synthetic PRIOR/L0; empirical zero errors cannot certify production safety.

The aggregate artifact is [cluster-calibration-20261013-final.json](cluster-calibration-20261013-final.json). It includes source hashes, all score thresholds, per-stratum metrics, input hashes, pair and cluster metrics, confidence intervals, and explicit limitations. No raw personal row values or individual record/entity IDs are exported.

## Source recovery

Four missing TC6/100k and TC8/100k CSVs were downloaded through the public Google Drive links into `artifacts/cluster-calibration-20261013/recovered`. Total recovered size is 7,785,083 bytes. All four match the original manifests' SHA-256 hashes. Original Downloads bundles and manifests remain unchanged; the harness reads existing source files plus a workspace recovery overlay.

| Recovered CSV | SHA-256 |
| --- | --- |
| TC6 `bill-2.csv` | `b0de0de95c259cc7966552afe5c5eac986fdd432a082bb4854c0588f819df4f5` |
| TC6 `crm-2.csv` | `8dd0be755cfe3c525d6d2339e77af8c014f3ea95725f68c212ce3292888f1d60` |
| TC8 `bill-2.csv` | `d58412aaa6b35d3832d0c5c3a4564c01c36e792e5a7a5f2aae92ea64ce3448ff` |
| TC8 `erp-1.csv` | `34bf6e2340586925f42d758192a7c75db8f905610eed10355d3d1bef1ee55342` |

Download sources and metadata are recorded in `artifacts/cluster-calibration-20261013/recovered/recovery.json`. The recovery utility verifies bytes before writing accepted CSVs. No credentials, API model calls, Docker, database service, or production mutation are involved.

## Evaluation method

The real decoder reads all 200,000 input records from 16 checksum-verified CSVs and checks file record counts. The real standardizer prepares the selected scenario records under `assessment_profile()`. Full TC6 covers 1,300 components and 67,200 exhaustive within-component pairs, including chain-3/5/10, star, two-cluster false bridge, positive controls, review/cannot-link, same-source, and size-19/20/21. Full TC8 scenario coverage is 800 components and 800 pairs across all eight placeholder/shared-contact strata.

Rules score real observation fields using component-local contact frequencies. Truth ownership is loaded after all matching assessments and only evaluates predictions. Scenario membership agrees with `truth/records.parquet`. Scenario edge weights are unused. No reviewer ACCEPTED decision or ground-truth-derived approval is supplied. Explicit scenario cannot-links and review vetoes remain separate permission constraints, even when ownership truth says MATCH.

The experimental gate requires an actual rule score at least the reported threshold, Group A, direct identifier evidence or a good name/contact anchor, no blocked or insufficient evidence, and no direct declared veto/cannot-link. Thresholds span quantiles of distinct observed score values without labels. Passing pairs receive a separately labeled experimental eligible route before existing safe cluster guards evaluate the complete component. This is an experimental change to routing, not an assertion that the production assessment grants automatic merge permission. Production assessment results remain available in the report for comparison.

Pair precision/recall evaluates proposed experimental edges. Cluster-pair precision/recall evaluates every pair co-clustered after guards, including transitive pairs. Exact cluster precision/recall requires identical predicted and ownership member sets and includes singletons. B-cubed precision/recall averages per-record overlap with the ownership cluster. Veto compliance and cluster size violations are reported independently from identity recall; forbidden ownership-positive pairs remain in the identity denominator.

Wilson 95% intervals accompany pair precision, cluster-pair precision, and the rate of components with a false merge. Pair observations within a component are dependent, so pair intervals are descriptive. Component intervals summarize this synthetic component distribution and do not account for real-world distribution shift, repeated threshold selection, or conditional risk among only attempted merges. There is no trained parameter, independent holdout, calibrated match probability, or chosen risk tolerance.

## Observed curve

The unchanged measured code completed all fourteen case/threshold evaluations with exit code 0 in 323.72 seconds. Input decoding, matching, and repeated component guard sweeps account for that wall time; it is not full-pipeline throughput. There are 18 distinct observed rule scores, ranging from 2.0 to 12.3.

| Rule-score threshold | TC6 proposed pairs | TC6 guarded positive pairs / 59,200 | TC6 guarded recall | TC8 proposed/guarded positive pairs / 600 | TC8 guarded recall |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2.0 | 1,900 | 1,700 | 2.872% | 400 | 66.667% |
| 2.5 | 1,900 | 1,700 | 2.872% | 400 | 66.667% |
| 4.3 | 1,900 | 1,700 | 2.872% | 400 | 66.667% |
| 6.0 | 1,900 | 1,700 | 2.872% | 400 | 66.667% |
| 8.3 | 1,700 | 1,500 | 2.534% | 400 | 66.667% |
| 11.130075 | 900 | 700 | 1.182% | 200 | 33.333% |
| 12.300001 | 0 | 0 | 0% | 0 | 0% |

Every nonempty route has observed pair precision 100% and guarded cluster-pair precision 100%, with zero cross-owner co-cluster pairs, veto/cannot-link violations, or size violations. Empty-route precision is undefined. At thresholds 2.0–6.0, the descriptive Wilson 95% lower bounds are 99.798% for 1,900 TC6 proposed pairs, 99.775% for 1,700 TC6 guarded pairs, and 99.049% for 400 TC8 proposed/guarded pairs. These are uncertainty summaries, not acceptance criteria.

At those lower thresholds, TC6 forms 1,100 multi-record clusters; TC8 forms 400. TC6's B-cubed precision/recall is 100%/42.628%, and exact cluster precision/recall is 28.889%/83.871%. TC8's corresponding values are 100%/87.5% and 66.667%/80%. Exact cluster measures include singletons, which explains their differences from pair metrics. Complete definitions and per-stratum metrics are in the JSON.

The actual gate recovers all within-component ownership positives in the bridge, chain-3/5/10, star, two-cluster bridge, same-source, and positive-control strata at the lower floors. The 100 review-veto and 100 cannot-link components each preserve a two-record cluster and refuse closure across the declared third-record constraint. Guards report their respective veto/cannot-link reasons 100 times each. Ownership-positive identity recall is one third in those two deliberately constrained strata.

All 300 size-19/20/21 components were assessed. The Group A/anchor gate selects zero pairs in these strata, so their 57,100 positive pairs dominate TC6's low aggregate recall. Zero size violations does not establish size-boundary merge behavior: the experiment abstains before a large union is proposed. This is evidence that score adjustment alone cannot recover those observations under this conservative gate.

The observed rate of a component with any false merge is 0/1,300 for TC6 and 0/800 for TC8. Its unconditional Wilson 95% upper bounds are 0.295% and 0.478%, respectively. Those denominators include abstaining components; they are not conditional error bounds for only attempted merges and cannot establish production risk tolerance.

After the measured run, control-only review changes added failure on changed source hashes, rejected recovery caches overlapping the readonly dataset tree, and rejected absolute/traversing manifest paths before download. Scoring, threshold selection, and cluster algorithms were unchanged. The JSON retains hashes of the exact unchanged code used for the measured run; [cluster-calibration-20261013-controls.json](cluster-calibration-20261013-controls.json) distinguishes those hashes from the current control-hardened script. Offline checks passed for traversal/absolute paths, input-tree cache rejection, changed-code failure, and constraint failure. Ruff and Python compilation passed.

## Reproduction

Run from the repository root in PowerShell:

```powershell
./.venv/Scripts/python.exe -X utf8 scripts/recover_cluster_benchmark.py --datasets 'C:/Users/nguye/Downloads/benchmark_datasets' --cache artifacts/cluster-calibration-20261013/recovered
./.venv/Scripts/python.exe -X utf8 scripts/calibrate_cluster_benchmark.py --datasets 'C:/Users/nguye/Downloads/benchmark_datasets' --cache artifacts/cluster-calibration-20261013/recovered --output docs/benchmark/cluster-calibration-20261013-final.json
```

Candidate generation, full-bundle retrieval recall, pipeline throughput, production publication/export, learned-model evaluation, and TC13 are outside this sweep. TC8 scenario selection includes zero JUNK records, so this does not certify junk detection. Wall time covers component scoring and repeated guarded sweeps only.
