# Offline cluster handoff validation, 2026-10-13

The final run completed with exit code 0, no runtime exceptions, and no observed cross-owner, declared veto/cannot-link, or cluster-size violations in the executed component probes. This is **qualified partial validation**, not a full-dataset benchmark PASS. All four inspected bundles are incomplete in this local copy.

The canonical aggregate artifact is [cluster-handoff-20261013-final.json](cluster-handoff-20261013-final.json). It records source/script hashes, unchanged code during the run, per-file SHA-256, counts, exclusions, assessment groups, full/partial snapshots, explicit negative-decision replay, and separate assumed-eligible guard probes. It contains no row values or individual record/entity identifiers.

## Integrity and scope

| Bundle | Present files matching manifest | Missing manifest-listed files |
| --- | --- | --- |
| TC6/100k | 17/19 | `input_p119/bill-2.csv`, `input_p119/crm-2.csv` |
| TC8/100k | 17/19 | `input_p119/bill-2.csv`, `input_p119/erp-1.csv` |
| TC13/100k | 16/19 | `evaluation/scenarios.jsonl`, `input_p119/bill-1.csv`, `truth/records.parquet` |
| TC13/1m | 17/19 | `input_p119/crm-2.csv`, `truth/entities.jsonl` |

All 67 present manifest-listed files across those four bundles match SHA-256. Missing files were neither generated nor substituted. TC13/100k did not execute. Independently, TC6/1m `input_p119/csv-1.csv` reproduces the README's known mismatch: expected `a8a04fe0d8e0d58ea6a041cc2ad7967626b76227e8709e4b5ed26e610b56be2d`, actual `2584887e5ad2fec3e444e9b6e27ef062f3a20d3ae866a9bb5348dd54e0a02726`. That bundle was not used for matching.

| Executed scope | Complete components | Assessed within-component pairs | Ownership-positive pairs |
| --- | ---: | ---: | ---: |
| TC6/100k | 218 of 1,300 declared | 601 | 408 |
| TC8/100k | 538 of 800 declared | 538 | 406 |
| TC13/1m, bounded reservoir | 18 | 102 | 18 |

TC6 covers bridge-phone-email 34, chain-3 31, chain-5 7, review-veto 33, cannot-link 31, same-source 51, and positive-control 31. No complete chain-10, star, two-cluster false-bridge, or size-19/20/21 component exists among the available inputs, so those benchmark scenarios remain untested.

TC8 covers real placeholder name 66, phone 66, email 70, January-1 DOB 67, common name 67, missing contacts 70, shared-household 66, and shared-accountant 66. The supplied scenarios select zero JUNK records; these results do not validate JUNK detection or all 10,000 JUNK rows.

TC13/1m covers three components each for shared-contact frequency 4/5/6, incorrect DOB, missing DOB, and province/ward/contact changes. The harness scans a bounded reservoir of 30 scenarios per stratum and executes at most three complete components of size at most 61. Contact/name block boundaries, Top-K distractors, identical/near text, and hot-200/hot-1000 retrieval remain untested. Candidate generation and full-bundle candidate recall were not measured.

## Method and outcomes

The real P-119 CSV decoder validated all 1,025,000 available input rows across the executed bundles. The real standardizer prepared selected records under `assessment_profile()`. Each complete component received exhaustive pair assessment and fresh full/partial snapshot computation. Frequencies and guard budgets are component-local. Stable source identity hashes pin record IDs; endpoints use canonical orientation. Dataset inputs remain read-only.

No reviewer ACCEPTED decision was supplied or invented. Actual assessment retains UNDECIDED overlays. The only scenario replay decisions are explicitly declared REJECTED review vetoes and cannot-links. Truth ownership is evaluated offline and never passed as matcher features, verified evidence, or review permission. After matching, scenario ownership was checked against `truth/records.parquet`: 22,410 selected scenario records agree, with zero mismatches.

Actual undecided full and partial computations produce **zero multi-record clusters** and **zero ownership-positive co-clustered pairs** across all 832 selected positive pairs. Actual partial publication excludes all 1,756 executed records as unresolved. Zero false merges here demonstrates abstention, not useful production merge recall or precision. Full snapshot singleton coverage is a reconstruction using `with_singletons`; full workflow completion gates and persisted publication were not executed.

Separate guard stress probes force only declared TC6 evidence edges to assumed MERGEABLE eligibility, retaining actual field comparisons and assessment groups. This is an oracle fixture assumption, not an engine finding or a steward approval. Those probes form 82 safe groups: 51 same-source groups and 31 positive-control groups, comprising 144 ownership-positive co-clustered pairs. No cross-owner or declared constraint pair co-clusters. All 218 reversed-record/edge probes produce identical safe memberships. Review-veto guards report `CLUSTER_REVIEW_VETO` in all 33 cases; cannot-link guards report `CLUSTER_CONFIRMED_CANNOT_LINK` in all 31 cases. Bridge/chain guards report missing evidence or pair permission conflicts.

The diagnostic [baseline artifact](cluster-handoff-20261013-baseline.json) captured seven chain-5 null-weight sorting TypeErrors before the handoff fix took effect. Source edits overlapped that run, so it is not an immutable before/after comparison. The final artifact is the clean post-fix run and reports no such exception.

## Reproduction and limits

From the repository root in PowerShell:

```powershell
./.venv/Scripts/python.exe -X utf8 scripts/verify_cluster_benchmark.py --datasets 'C:/Users/nguye/Downloads/benchmark_datasets' --output docs/benchmark/cluster-handoff-20261013-final.json --allow-incomplete --check-tc6-1m-hash
```

Without `--allow-incomplete`, the harness refuses matching on incomplete bundles. Even with that option, a changed matcher input is blocked. Both refusal paths were checked independently. The final command exited 0; Ruff and Python compilation also passed.

The data is synthetic PRIOR/L0 and cannot establish a production auto-merge threshold. Ownership positives are not equivalent to permission to merge, particularly in deliberate veto/cannot-link scenarios. These measurements do not cover approved reviewer exceptions, a full 100k/1m pipeline, candidate recall, production import/publication/export, database services, Docker, LLMs, or learned-model evaluation. The measured component wall times are not full-dataset performance claims.
