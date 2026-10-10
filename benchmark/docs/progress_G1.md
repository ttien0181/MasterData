# G1 Status

G1 — Data, Contracts & Evaluation Protocol: **BLOCKED** (2026-09-30). The G1 foundations and required tests pass, but the gate cannot pass while the only available labeled partition is test and complete entity gold is unverified.

## Implemented

- CONTRACT-01 — Global dataset/source/raw record IDs and order-independent canonical pair IDs retain input/source orientation. Evidence: `standalone/contracts/core.py`; `tests/test_g1.py::test_pair_canonicalization_is_order_independent_and_keeps_orientation`.
- CONTRACT-02 — Scorer-facing `Record` and `RecordFeatures` exclude IDs and annotations; scorer consumes only field comparisons. Evidence: `standalone/contracts/core.py`, `standalone/contracts/interfaces.py`; `tests/test_g1.py::test_adapter_preserves_labels_and_separates_annotations`, `test_pair_scorer_signature_cannot_receive_record_or_gold`.
- CONTRACT-03 — Positive, negative, and unknown are separate; malformed labels fail. Evidence: `standalone/contracts/labels.py`; `tests/test_g1.py::test_pair_label_parser_separates_positive_negative_unknown`.
- CONTRACT-04 — Similarity, log-likelihood ratio, and probability are distinct validated score types. Evidence: `standalone/contracts/core.py`; `tests/test_g1.py::test_pair_scorer_signature_cannot_receive_record_or_gold`.
- CONTRACT-06, CONTRACT-07, CONTRACT-08 — Dedupe, clean-clean, and multi-source pair universes are explicit; source uniqueness is not assumed. Evidence: `standalone/datasets/universe.py`, `datasets/manifests/masterone_test_v1.json`; `tests/test_g1.py::test_legal_pair_universe_dedupe_clean_clean_and_multisource`, `test_manifest_pair_universe_does_not_assume_source_uniqueness`.
- CONTRACT-09 — Pair/entity gold coverage is explicit and insufficient coverage blocks pair-universe and cluster evaluation. Evidence: `standalone/contracts/core.py`, `standalone/evaluation/protocol.py`; `tests/test_g1.py::test_evaluation_coverage_gates_keep_unknown_unlabeled_pairs_blocked`.
- CONTRACT-11 — Dataset schema and config are validated and frozen by version/hash. Evidence: `standalone/datasets/masterone.py`, `standalone/config/datasets/masterone_test.json`, `datasets/manifests/masterone_test_v1.json`; `tests/test_g1.py::test_adapter_rejects_schema_drift`, `test_dataset_manifest_locks_current_config_and_raw_hashes`.
- DATA-I01, DATA-I02, DATA-I03 — Independent MasterOne adapter maps the requested fields, preserves 960 positive and 240 negative labels, preserves split, and keeps annotations in gold/evaluation structures. Evidence: `standalone/datasets/masterone.py`; `tests/test_g1.py::test_adapter_preserves_labels_and_separates_annotations`.
- DATA-I08 — All three cross-source slices contain positive and negative examples; the negative cases are categorized in the audited hard-negative distribution. Evidence: `docs/dataset_audit.md`, `datasets/manifests/masterone_test_v1.json`; `tests/test_g1.py::test_real_masterone_audit_and_manifest_inputs`.
- DATA-R01 through DATA-R07 — Local reference bundle is explicit, hashed/versioned, excludes `.bak`, separates column/admin aliases, loads versioned units and directed old-to-new mappings, audits integrity/collisions, preserves ambiguity, and does not invent an inverse mapping. Evidence: `standalone/references/local.py`, `datasets/manifests/data_reference_v1.json`; `tests/test_g1.py::test_reference_loader_preserves_ambiguity_and_ignores_backup`.
- QA-01 — Label parsing, canonical/reversed pair identity, duplicate/reversed pairs, self-pairs, and legal pair universes are tested. Evidence: `tests/test_g1.py::test_pair_label_parser_separates_positive_negative_unknown`, `test_pair_canonicalization_is_order_independent_and_keeps_orientation`, `test_legal_pair_universe_dedupe_clean_clean_and_multisource`, `test_audit_detects_reversed_duplicate_self_dangling_and_bad_label`.
- QA-02 foundation — Scorer contract, annotation separation, metadata mutation, and ID permutation at the prediction-input boundary are tested. Actual prediction invariance awaits a scorer; QA-02 remains unchecked. Evidence: `tests/test_integrity_review.py::test_prediction_boundary_ignores_gold_mutation_and_id_bijection`.
- QA-03 — Entity overlap, record overlap, cross-partition pair endpoints, missing partitions, and missing entity labels/endpoints are detected. Evidence: `standalone/datasets/splits.py`; `tests/test_g1.py::test_split_integrity_reports_entity_record_and_endpoint_leakage`, `test_split_checker_accepts_entity_disjoint_partition`, `test_split_checker_blocks_missing_endpoint_or_entity_gold`.

## Tests

- `python -m pytest -p no:cacheprovider --basetemp=.cache/pytest-g1 tests/test_g1.py` — **19 passed**, Windows, Python 3.11.9.
- `python -m pytest -p no:cacheprovider --basetemp=.cache/pytest-g0 tests/test_g0.py` — **14 passed**, Windows, Python 3.11.9.
- Final combined smoke: `python -m pytest -p no:cacheprovider --basetemp=.cache/pytest-final tests/test_g0.py tests/test_g1.py` — **33 passed**.
- `python scripts/audit_g1_data.py` — completed; 4,213 records, 1,200 listed pairs, legal pair universe 8,872,578, no structural issues; reference version `local-reference-sha256:abb0d846d30fffd53cebea3d13aa2f5ba0e1273aad4d76464114ddc82edea463`; `.bak loaded: False`.
- Test scratch, temp, and bytecode paths were confined to `benchmark/.cache`; pytest cache and bytecode writing were disabled.

## Evidence

- Audit report: `docs/dataset_audit.md`.
- Protocol: `docs/evaluation_protocol.md`.
- Frozen dataset and reference manifests: `datasets/manifests/masterone_test_v1.json`, `datasets/manifests/data_reference_v1.json`.
- Code: `standalone/contracts/`, `standalone/datasets/`, `standalone/references/`, `standalone/evaluation/`.
- Tests: `tests/test_g1.py`, `tests/test_g0.py`.

## Blocked

- **Training/tuning experiments are BLOCKED:** the dataset has only 1,200 labeled pairs and every pair is in `test`; no independent train or dev partition exists. Do not split this test sample and call the remainder an untouched holdout.
- **Complete pair-universe metrics are BLOCKED:** listed labels are sampled; the other 8,871,378 legal pairs remain unknown, not negative.
- **Cluster metrics and complete-entity evaluation are BLOCKED:** generator-provided `entity_id` exists but its truth has not been independently audited/adjudicated.
- **Public benchmark track is BLOCKED:** no public dataset with verified source, license, version, and label protocol is installed.
- DATA-I04 historical test usage is not recorded in the source data, so prior tuning history cannot be certified. DATA-I05/I06/I07 and DATA-I10–I13 remain incomplete or blocked. Reference legal/upstream provenance (DATA-R08) and independent address gold (DATA-R09) are unavailable.
- CONTRACT-05 cluster result contract and later normalization/algorithm/test tasks are outside the G1 implementation evidence and remain unchecked. No G2/G3 work was started.
- Integrity review reopened QA-02 because no implemented scorer exists for an actual prediction invariance test. The review fixed the representation and manifest/coverage guards; the dataset and gold blockers remain.

## Dataset assumptions

- Files audited: `datasets/masterone_test/records.csv` (4,213 rows) and `pairs.csv` (1,200 rows), UTF-8, SHA-256 and exact schemas frozen in the dataset manifest.
- Sources are BILL (1,407), CRM (1,408), ERP (1,398). Pair labels are 960 positive and 240 negative; every listed pair is assigned to `test`.
- There are 1,200 non-empty record entity IDs but those labels are generator provenance, not verified complete entity gold. Same-source duplicates are present, so same-source comparisons remain in the legal universe.
- Missing values include 137 empty phone fields and 273 empty email fields. The full legal multi-source universe is 8,872,578 pairs under all same-source and cross-source combinations.
- Raw files remain unchanged; manifests store SHA-256, schema, encoding, row count, version, task, source list, gold coverage, and provenance caveats.

## Leakage controls

- Canonical scorer feature allowlist is `name`, `phone`, `email`, and `address`. Source is available for task routing; record IDs are opaque join/trace keys, not features and are not tokenized or compared.
- `entity_id`, `address_id`, `entity_a`, `entity_b`, `is_match`, `noise_type`, `difficulty`, `case_type`, `hard_negative_type`, `temporal_case`, `split`, and generator labels are evaluation/gold metadata, outside scorer input.
- Unknown labels stay unknown. Unlisted pairs do not become negative examples. Fit accepts train only, tuning accepts dev only, and test is rejected by both guards.
- Split integrity checker detects entity overlap, record overlap, pair endpoints crossing partitions, unassigned endpoints, and missing required partitions.

## Gate decision

**BLOCKED** — contracts, adapter, audit, manifests, references, leakage/split guards, and required tests are implemented and passing. G1 cannot PASS because there is no train/dev data and the available entity labels are not independently verified. No results or labels were fabricated.

## Integrity review addendum (2026-09-30)

See `docs/SOL_REVIEW_G0_G1.md` for reproduced defects, fixes, checklist corrections, and final regression results. The frozen raw hashes and reference version were rechecked without changing raw files. G1 remains BLOCKED on train/dev and independently verified entity gold.

The final review suite ran `python -m pytest -q -p no:cacheprovider --basetemp=.cache/review-final tests` with **49 passed**, including the redirected manifest symlink/junction regression. The corrected reference manifest identifies the administrative aliases CSV as UTF-8 with BOM; its SHA-256 and reference version are unchanged.
