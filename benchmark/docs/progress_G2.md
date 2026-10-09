# G2 Status

G2 — Harness, Metrics & Basic Baselines: technical implementation **PASS**, overall handoff **BLOCKED** pending recovery and update of the source checklist. The G0/G1 integrity review reports that all reproduced P0 issues were fixed and regression tests pass; its `NOT_READY_FOR_G2` verdict reflects missing train/dev, unknown prior test-use history, and unverified entity gold. Those data blockers prohibit learning/tuning and complete-gold claims, but do not block fixed deterministic baselines and partial-label metrics.

## Implemented

- **MET-01, MET-04–MET-07, MET-09–MET-10, MET-23:** Pair metric spec and evaluator. Confusion matrix uses known labeled pairs only; predictions outside that labeled set are counted as unlabeled and ignored. Unknown labels never become negatives. Undefined values carry N/A/BLOCKED plus a reason. Brier requires typed `Probability` and explicit calibrated declaration. Evidence: `docs/metrics_spec.md`; `standalone/evaluation/metrics.py`; `tests/test_g2.py`.
- **MET-02:** Blocking metric API validates unique legal candidates and labels recall on sampled positives as `sampled_labeled_positive_candidate_recall`. Evidence: `standalone/evaluation/metrics.py::blocking_metrics`; `tests/test_g2.py::test_hand_computed_blocking_recall_and_reduction_ratio`.
- **MET-12–MET-15, MET-24:** Pairwise cluster counts, B³, micro purity, over/under-merge, singleton behavior, exact membership checks, verified entity-gold gate, and cluster validation coverage/unsafe-rate contract. Uses contingency counts without pair-closure materialization. Evidence: `standalone/evaluation/clusters.py`; `tests/test_g2.py::test_hand_computed_cluster_metrics_split_merge_and_singletons`, `test_cluster_gold_gate_validation_coverage_and_membership_contract`.
- **MET-20:** Resource timing foundation has non-overlapping named stage calls and a separate inclusive end-to-end measure. It rejects duplicate stage measurement and reports unrun stages explicitly. Evidence: `standalone/runtime/resources.py`; `tests/test_g2.py::test_runtime_measurements_reject_double_counting`.
- **MET-21 (partial):** Best-effort process RSS via optional psutil or standard-library `resource` fallback, stage CPU time, and throughput. RSS may be N/A; child-process aggregation is not implemented. Evidence: `standalone/runtime/resources.py`; current Windows CLI output reports RSS unavailable.
- **MET-23 / CLI-06 / CLI-07 / CLI-13:** Terminal report to stdout, progress to stderr, explicit N/A reasons, nonzero invalid/unsupported command status, and no implicit report/prediction export. Evidence: `standalone/cli.py`; `tests/test_g2.py::test_g2_cli_runs_real_pair_track_and_blocks_unavailable_track`.
- **B0:** Deterministic exact/rule implementation with evidence per decision and typed similarity score. Evidence: `standalone/baselines/exact.py`; `docs/baselines_G2.md`; baseline determinism test.
- **B1:** **Weighted V1 reimplementation**, with documented field similarity, weights, missing policy, fixed thresholds, and version. It is independent of application code and returns `Similarity`, not probability. No fit or test tuning occurs. Evidence: `standalone/baselines/weighted_v1.py`; `docs/baselines_G2.md`; baseline test.
- **Runner/CLI:** Real `datasets list`, `datasets audit --dataset masterone_test`, and `run --dataset masterone_test --systems exact,weighted --track pairs`. Unsupported end-to-end, suite, ablate, compare tracks fail explicitly. Evidence: `standalone/evaluation/runner.py`, `standalone/cli.py`; root CLI smoke test.

## Tests

- `python -m pytest -q -p no:cacheprovider --basetemp=.cache/g2-final tests` — **62 passed** (G0, G1, integrity review, and G2).
- Initial G2 fixture run found four mismatches in test expectations/readme and one additional B1 all-missing routing issue; the issue was fixed and the full suite rerun.
- `python benchmark/run.py datasets list` from repository root — **PASS**.
- `python benchmark/run.py datasets audit --dataset masterone_test` from repository root — **PASS**, 4,213 records, 1,200 labeled pairs, split test only, structurally valid.
- `python benchmark/run.py run --dataset masterone_test --systems exact,weighted --track pairs` from repository root — **PASS**, two real deterministic pair baseline runs, no artifacts written.
- `python run.py datasets list` from `benchmark/` and a different working directory — **PASS** (covered by G0 cwd test and G2 integration test).

## Evidence

- Hand-computed fixtures: `tests/test_g2.py` covers perfect, all-wrong, no predicted positives, no positive gold, all-negative gold, unknown and unlisted pairs, AP, calibrated/uncalibrated Brier, frozen coverage threshold, candidate recall/reduction/duplicate/illegal candidates, perfect/split/merged/singleton clusters, incomplete cluster validation, missing-feature routing, deterministic baselines, timing double-count rejection, and CLI output/blocking.
- Specification: `docs/metrics_spec.md`.
- Baseline definitions: `docs/baselines_G2.md`.
- Real-data audit and manifest remain `docs/dataset_audit.md` and `datasets/manifests/masterone_test_v1.json`; raw CSV hashes were not changed.
- Test scratch and Python temp paths used `benchmark/.cache`; test run disabled bytecode and pytest cache.

## Result summary

The command evaluated only the 1,200 supplied labeled test pairs (960 positive, 240 negative) from a legal universe of 8,872,578 pairs. B0 exact/rule: precision **0.9941**, recall **0.8844**, F1 **0.9361**, average precision **0.9717**. Weighted V1 reimplementation: precision **1.0000**, recall **0.1062**, F1 **0.1921**, average precision **0.9931**. Both Brier scores are **N/A** because these baselines produce similarities, not calibrated probabilities. Weighted review rate was **0.6442**. These are descriptive internal test-sample results; they are not an untouched holdout or a claim of population performance.

## Blocked / remaining scope

- No train/dev partitions exist. Learned models, threshold tuning, and coverage-at-target-precision are BLOCKED. Fixed baseline values were set in code/docs without test tuning.
- Complete pair labels and independently verified entity gold are unavailable. Full-universe pair metrics and entity-based cluster metrics return BLOCKED/N/A. The cluster API and metric formulas are implemented but cannot be evaluated on this dataset.
- No candidate generator is part of B0/B1 fixed-pair CLI; blocking metric API is implemented, but no blocker result is claimed. Per-block-size quantiles, violation counts, normalization metrics, API cost, GPU/child RSS, and report-stage timing remain unsupported.
- Review report `docs/SOL_REVIEW_G0_G1.md` has no unresolved P0 findings; its overall G1/data gate remains blocked as described above.
- During checklist evidence update, a Windows newline error truncated `docs/BENCHMARK_IMPLEMENTATION_CHECKLIST.md` to zero bytes. It was not tracked by Git and no workspace backup was found. I did not replace it with an invented checklist. Restore the source checklist before treating the handoff documentation as complete.

## Dataset assumptions

- MasterOne manifest is required and source files are frozen by SHA-256.
- All listed pair labels are `test`; unlisted legal pairs are unknown.
- `entity_id` is generator provenance, not independently verified complete gold.
- Current exact/weighted scores are a fixed-pair descriptive evaluation; the pair CLI does not report blocking recall or full-universe TN.

## Leakage controls

- Baseline `score()` methods receive only two `Record`s and read only allowlisted business fields. They do not receive pair IDs, labels, or annotations. The runner attaches canonical pair ID to `PairPrediction` after scoring.
- Gold labels are read for evaluation only. Score evaluation is restricted to known IDs in L; unlisted predictions are ignored and counted separately.
- Record IDs, entity IDs, split, case/noise/difficulty metadata are absent from baseline score signatures and fields.
- The existing dataset feature allowlist remains name, phone, email, and address. B0/B1 thresholds are fixed and no test-set tuning occurs.

## Gate decision

**FAIL for formal handoff; technical G2 implementation passes its executable criteria.** Checklist recovery is required because the source-of-truth checklist was accidentally truncated during the attempted evidence update. G2 code/tests/results are in place, but do not use the missing checklist as evidence. No G3 work was started.
