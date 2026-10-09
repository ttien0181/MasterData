# Summary

G0/G1 integrity review, 2026-09-30. Initial G0/G1 suite: **33 passed**. A new regression suite reproduced **9 failures** before fixes. After fixes, the full benchmark test suite passes; the frozen source audit still reports 4,213 records, 1,200 labeled test pairs (960 positive, 240 negative), and 8,872,578 legal pairs. Raw files and frozen hashes were unchanged.

Repository status contains no paths outside `benchmark/`. The retained legacy files `adapters/base.py`, `adapters/febrl.py`, `adapters/mapped_csv.py`, `metrics/core.py`, and `runners/baseline.py` import `src`; the standalone entry, all standalone modules, and the G1 audit script do not import `src` or `app`. The standalone module import regression blocks application and optional dependency imports.

Leakage review covered dataclasses, adapter dictionaries, the feature allowlist in dataset config, stage interfaces, and trace keys. No dataframe, normalization algorithm, scorer implementation, or prediction cache exists yet. The current raw IDs contain source prefixes and sequence numbers, so prediction stages receive indexes and business fields through `build_prediction_inputs()`; opaque IDs remain in the separate trace used for joins and evaluation.

# P0 Findings

## P0-01 — Output directory scope was bypassable

- **ID / severity:** P0-01 / P0.
- **Files:** `standalone/runtime/paths.py`, `scripts/audit_g1_data.py`.
- **Issue:** `output_path()` accepted an absolute path under `benchmark/docs/`, and `cache_path()` accepted one under `benchmark/results/`. Audit writers used direct `Path.write_text()` and could follow an output symlink/junction outside the benchmark.
- **Evidence:** The pre-fix reproduction returned `docs/escape.json` and `results/escape.json`; `tests/test_integrity_review.py::test_output_and_cache_helpers_stay_in_their_own_directories` failed before the fix.
- **Fix:** Enforce resolved paths under the designated results/cache scope; reject redirected scope directories. Audit artifact writers guard the manifest and docs scopes before reading, creating parents, or writing.
- **Validation:** `test_output_and_cache_helpers_stay_in_their_own_directories`, `test_audit_artifact_writers_reject_outside_paths`, `test_audit_writer_rejects_redirected_manifest_directory`, and the existing traversal/symlink/junction G0 tests pass.

## P0-02 — Equal pair counts falsely proved complete gold

- **ID / severity:** P0-02 / P0.
- **Files:** `standalone/evaluation/protocol.py`, `standalone/contracts/core.py`.
- **Issue:** Declaring `complete_pair_labels` and supplying equal labeled/legal counts returned PASS even for duplicated or missing pair keys. A complete universe metric could then treat unknown pairs as known.
- **Evidence:** Pre-fix `pair_universe_coverage(GoldCoverage(pair_labels=COMPLETE_PAIR_LABELS), 2, 2)` returned PASS without any pair identities.
- **Fix:** Complete coverage now requires exact equality of unique legal pair IDs and known labeled pair IDs. Unknown labels, duplicates, missing keys, and count-only declarations return BLOCKED. GoldCoverage rejects semantically invalid coverage enum placements.
- **Validation:** `test_complete_pair_coverage_requires_actual_key_equality` and `test_evaluation_coverage_gates_keep_unknown_unlabeled_pairs_blocked` pass.

## P0-03 — Prediction boundary admitted annotation-shaped values

- **ID / severity:** P0-03 / P0.
- **Files:** `standalone/contracts/core.py`, `standalone/contracts/interfaces.py`, `tests/test_integrity_review.py`.
- **Issue:** `FieldComparison({'entity_id': 'same'})` and nested dictionaries in allowlisted feature slots were accepted. The earlier metadata/ID test compared two manually constructed feature views and did not exercise the adapter boundary.
- **Evidence:** Both constructions succeeded before the fix; the new comparison/feature regression failed in the red run.
- **Fix:** Feature values must be strings or None, comparison keys must be allowed business fields, and the comparison mapping is immutable. `build_prediction_inputs()` emits only candidate source, business fields, and pair indexes; IDs are held in a separate orchestrator trace. The adapter retains annotations separately, including empty values.
- **Validation:** `test_comparison_and_feature_contracts_reject_nested_gold` and `test_prediction_boundary_ignores_gold_mutation_and_id_bijection` pass. The latter mutates entity/address IDs, generator annotations, pair label/split, and record IDs while keeping business fields fixed; stage inputs remain equal and the ID bijection preserves pair joins.

## P0-04 — Frozen test could load without its manifest

- **ID / severity:** P0-04 / P0.
- **Files:** `standalone/datasets/masterone.py`, `scripts/audit_g1_data.py`.
- **Issue:** The adapter silently skipped manifest verification when the file was absent and accepted a `train` split for the frozen `internal-test-v1` dataset.
- **Evidence:** Both conditions were accepted in the red regression run.
- **Fix:** Manifest verification is required by default; fixture/audit bootstrapping uses an explicit override. The adapter rejects non-test splits for this version, validates manifest file entries and ordered schema, and guards raw/config/manifest input paths. The audit script verifies frozen output content before accepting changes.
- **Validation:** `test_adapter_requires_frozen_manifest_by_default`, `test_test_dataset_rejects_split_relabeling`, and the original config/hash tests pass; the real frozen audit completes without drift.

# P1 Findings

## P1-01 — ID framing allowed collisions

- **ID / severity:** P1-01 / P1.
- **Files:** `standalone/contracts/core.py`.
- **Issue:** Concatenating record ID components with `::` and pair endpoints with NUL allowed distinct inputs to hash to the same identity.
- **Evidence:** Before the fix, `global_record_id('a::b','c','d') == global_record_id('a','b::c','d')` was true.
- **Fix:** Percent-encode each record ID component and hash an unambiguous JSON array of canonical pair endpoints. Dataset bundles reject duplicate record/pair IDs.
- **Validation:** `test_record_and_pair_identity_encodings_have_no_delimiter_collisions` and existing order/orientation tests pass.

## P1-02 — Reference candidates collapsed across versions

- **ID / severity:** P1-02 / P1.
- **Files:** `standalone/references/local.py`, `standalone/contracts/interfaces.py`.
- **Issue:** Mapping candidates were keyed only by target unit ID. Two target versions sharing that ID collapsed to one and could appear resolved.
- **Evidence:** The red fixture with `(NEW_A, same)` and `(NEW_B, same)` returned one candidate and `resolved`.
- **Fix:** Candidate keys include target version and unit ID. Multiple source versions or targets remain ambiguous. The provider accepts an explicit `as_of` date and does not apply mappings before their effective date.
- **Validation:** `test_reference_candidates_preserve_same_id_across_target_versions`, `test_reference_integrity_rejects_invalid_date_version_and_keeps_many_to_one`, and the real ambiguity/.bak test pass.

## P1-03 — Invalid reference mappings remained usable

- **ID / severity:** P1-03 / P1.
- **Files:** `standalone/references/local.py`.
- **Issue:** The old audit counted invalid mapping dates and missing units, but `load_local_references()` still returned a provider. Missing/invalid versions and date mismatches were not rejected.
- **Evidence:** Static loader path reached provider construction without checking audit findings; a synthetic invalid date/version fixture exercises the repaired validator.
- **Fix:** `validate_reference_bundle()` rejects critical referential, duplicate, version, direction, status, target, and effective-date defects. Expected alias collisions remain multi-candidate results. The CSV reader rejects malformed row widths.
- **Validation:** Invalid date, version, and target-date mismatch cases fail clearly; the real reference bundle validates and remains at `local-reference-sha256:abb0d846d30fffd53cebea3d13aa2f5ba0e1273aad4d76464114ddc82edea463`. `.bak` is not loaded.

## P1-04 — Structural audit and legal universe could misstate validity

- **ID / severity:** P1-04 / P1.
- **Files:** `standalone/datasets/audit.py`, `standalone/datasets/universe.py`, `scripts/audit_g1_data.py`.
- **Issue:** An empty record join key could leave `structurally_valid=True`; `iter_pairs()` trusted declared source counts even when actual records differed. The audit script could return success after a structural defect.
- **Evidence:** `test_audit_marks_empty_record_join_key_invalid` failed before the fix; the former `iter_pairs()` lacked count/ID validation.
- **Fix:** Critical empty join/split fields invalidate the audit, malformed CSV width fails, legal universe iteration verifies source counts and unique record IDs, and the audit script exits nonzero before writing manifests when structural checks fail.
- **Validation:** `test_audit_marks_empty_record_join_key_invalid`, `test_legal_universe_rejects_count_and_metadata_mismatch`, and the real 8,872,578-pair universe test pass.

## P1-05 — Reference manifest misstated one file's encoding

- **ID / severity:** P1-05 / P1.
- **Files:** `standalone/references/local.py`, `datasets/manifests/data_reference_v1.json`.
- **Issue:** The administrative aliases CSV starts with a UTF-8 BOM, while the loader hardcoded `UTF-8` in every reference file manifest.
- **Evidence:** Its first three raw bytes are `EF BB BF`; the previous manifest entry said `UTF-8`.
- **Fix:** Detect BOM from each loaded file's raw bytes and correct that manifest entry to `UTF-8 with BOM`. The source file and reference SHA-256 version did not change.
- **Validation:** `test_reference_manifest_detects_actual_bom_encoding` and `python scripts/audit_g1_data.py` pass; the frozen manifest comparison now matches.

# P2 Findings

## P2-01 — README advertised an absent downloader

- **ID / severity:** P2-01 / P2.
- **Files:** `README.md`, `tests/test_g0.py`.
- **Issue:** A copyable command targeted the missing `datasets/download_febrl.py`.
- **Evidence:** `git status` already reported that file deleted before this review; the old README contained the command.
- **Fix:** README now states acquisition is unavailable and identifies the prerequisites for legacy runner examples.
- **Validation:** `test_readmes_document_only_real_g0_cli_surface` passes.

## P2-02 — Three checklist claims exceeded available evidence

- **ID / severity:** P2-02 / P2.
- **Files:** `docs/BENCHMARK_IMPLEMENTATION_CHECKLIST.md`, `docs/progress_G0.md`, `docs/progress_G1.md`.
- **Issue:** ARCH-04 claimed a completed algorithm/experiment registry although its entries are pending, ARCH-05 claimed verified lazy optional adapters although only dependency buckets/core isolation exist, and QA-02 claimed actual prediction invariance although no scorer has been implemented.
- **Evidence:** Algorithm entries and executable experiment registration are absent; optional requirement files contain comments only; the previous QA-02 test compared feature views without invoking an adapter or scorer.
- **Fix:** Reopened all three checkboxes and recorded the partial evidence. The new prediction boundary regression proves representation invariance; actual prediction invariance remains a later test.
- **Validation:** Checklist and progress files state the same limitations; core optional-import and prediction-boundary tests pass.

# Fixes Applied

- Scoped path and audit artifact guards; standalone and audit inputs resolve inside `benchmark/`.
- Stronger ID, feature, score, gold-coverage, split, and legal-universe contracts.
- Manifest-required MasterOne loading and explicit test-only split policy.
- Version-aware, date-aware reference resolution with fail-closed source validation.
- README correction, checklist evidence updates, and G0/G1 progress addenda.

# Tests Executed

- Baseline: `python -m pytest -p no:cacheprovider --basetemp=.cache/review-baseline tests/test_g0.py tests/test_g1.py` — **33 passed**.
- Red reproduction: `python -m pytest -q -p no:cacheprovider --basetemp=.cache/review-red tests/test_integrity_review.py` — **9 expected failures, 1 passed** before fixes.
- Final full suite: `python -m pytest -q -p no:cacheprovider --basetemp=.cache/review-final tests` — **49 passed**. `PYTHONDONTWRITEBYTECODE`, `PYTHONNOUSERSITE`, and TMP/TEMP/TMPDIR were set with temp paths under `benchmark/.cache`.
- `python scripts/audit_g1_data.py` — **PASS**, 4,213 records, 1,200 listed pairs, 8,872,578 legal pairs, no structural defects; frozen manifests verified; `.bak loaded: False`.
- `git diff --check` and repository-root `git status --short --untracked-files=all` — no modified/untracked paths outside `benchmark/`.

# Checklist Corrections

- Reopened **ARCH-04**, **ARCH-05**, and **QA-02**. Their completed foundations and remaining work are recorded beside the unchecked tasks.
- Checked **AUDIT-01, AUDIT-03, AUDIT-05, AUDIT-06, QA-05** after verifying code, tests, and evidence.
- Strengthened evidence for **SCOPE-08, CONTRACT-01, CONTRACT-09, CONTRACT-11, DATA-R01, DATA-R05, QA-03**. Other checked G0/G1 tasks were reviewed against their cited files/tests.

# Remaining Blockers

- Internal pairs are all `test`; independent entity-disjoint train/dev data and prior test-use history are unavailable. Learned or tuned components remain BLOCKED.
- Generator `entity_id` has not been independently adjudicated as complete entity gold. Complete cluster/entity metrics remain BLOCKED; unlisted legal pairs remain unknown.
- Public benchmark data/license provenance and independently verified address-normalization gold are unavailable.
- Actual scorer prediction invariance, optional adapter lazy imports, and end-to-end metrics require later implementations. The retained legacy modules still import `src` and must stay outside the standalone path.
- Path guards are pre-open checks. Future concurrent writers and subprocesses must use guarded writes and their own filesystem review before being trusted.
- Exact complete-pair key comparison materializes key sets. A future large complete-gold track needs a bounded-memory exact validation strategy before scale runs.

# Verdict

**NOT_READY_FOR_G2** — all reproduced P0 defects are fixed and regression tests pass, but the G1 data gate remains BLOCKED by missing train/dev and independently verified entity gold. No result or label was fabricated.
