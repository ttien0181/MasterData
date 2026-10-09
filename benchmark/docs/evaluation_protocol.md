# Evaluation protocol — G1/G2

## G2 pair-run protocol

The supported command is `python benchmark/run.py run --dataset masterone_test --systems exact,weighted --track pairs`. It loads the frozen MasterOne manifest and evaluates only the explicitly listed labeled pair IDs. Positive and negative labels are consumed by the evaluator after B0/B1 prediction; predictions for unlisted legal pairs do not contribute to pair metrics. Unknown rows stay outside the confusion matrix.

All available pair labels are on the internal `test` split, and prior test-use history is unknown. Therefore CLI values are descriptive sample results, not untouched holdout estimates. B0 and Weighted V1 use their versioned fixed rules/thresholds, perform no fitting, and do not tune from test results. `Similarity` values are not probabilities, so calibration metrics are N/A. Target-precision coverage is BLOCKED until a threshold is selected on independent dev data. End-to-end/blocking/cluster tracks remain explicitly unsupported by the pair CLI.

See [metrics_spec.md](metrics_spec.md) for formulas and undefined policies, [baselines_G2.md](baselines_G2.md) for B0/B1 decisions, and [progress_G2.md](progress_G2.md) for executed commands and measured results.

## Dataset and label status

`masterone_test` is an internal, team-generated multi-source dataset. Its frozen source files currently contain 4,213 records and 1,200 listed labeled pairs. The pair labels are 960 positive and 240 negative, all marked `test`. The sampling frame/probabilities and independent adjudication history are not present in the files.

The 1,200 rows are **sampled labeled pairs**, not complete labels over the legal universe. An unlisted pair is unknown and must never be assigned negative. `entity_id` is available in the records source but is generator provenance with no independent entity audit; cluster metrics and complete-entity claims are blocked. Pair/entity consistency checks are structural checks against the same internal annotations and do not establish label truth.

## Permitted and blocked use

- The test pairs may be used for a declared final descriptive evaluation if the protocol and parameters were frozen without test feedback. The workspace contains no history proving whether this condition holds, so any post hoc tuning invalidates an unbiased final-test claim.
- Training, calibration, feature selection, and threshold tuning on `masterone_test` are blocked. No train/dev labels are present. Never repartition the 1,200 test pairs and call the remainder an untouched holdout.
- Learned baselines, threshold selection, calibration metrics, and train/dev ablations remain `BLOCKED` until new entity-disjoint train and dev labels are collected or generated under a documented synthetic protocol.
- Full legal-universe pair metrics remain `BLOCKED`; current pair coverage is sampled. Metrics on the listed labeled sample, if later implemented, must be reported as sampled-label results and cannot infer outcomes for unlisted pairs.
- Cluster metrics remain `BLOCKED` until complete entity gold is independently verified. The `entity_id` consistency check is not such verification.
- Public comparison remains `BLOCKED`: `datasets/public_dataset/` is empty and FEBRL source metadata exists without acquired data in this workspace. Never describe the internal generated set as an independent public benchmark.

## Split policy

Future train/dev/test partitions must be assigned by global entity before pair generation. Every record for an entity belongs to one partition; both endpoints of every labeled pair must belong to that same partition. The checker in `standalone/datasets/splits.py` reports entity overlap, repeated record assignment across pair splits, cross-partition endpoints, split mismatch, and missing required partitions. No derived split is emitted from the current test-only files.

`validate_fit_split()` accepts train only; `validate_tuning_split()` accepts dev only. Both reject test.

If train/dev data are created later, record the generator/source version, seed, transformation rules, entity IDs in a protected gold artifact, and manifest/hash. Keep those entities disjoint from frozen test entities. Keep synthetic gold clearly labeled synthetic and report it separately from adjudicated human gold.

## Pair universe

The internal task is `multi_source`. With 4,213 rows across BILL (1,407), CRM (1,408), and ERP (1,398), the declared legal universe includes within-source combinations and cross-source combinations because source-level uniqueness has not been established. This universe has 8,872,578 possible unordered pairs. The 1,200 labeled pairs are a small subset; universe coverage is not complete.

The generic contracts support dedupe, two-source clean-clean, and multi-source universes. A clean-clean protocol must declare its two sources. No source uniqueness constraint is applied to clusters based solely on task type; it requires dataset evidence.

## Leakage and feature policy

Scorer-facing `Record` has a global join/trace ID, a source label, and an allowlisted field map. `RecordFeatures` omits IDs and source. The only feature fields currently allowed are `name`, `phone`, `email`, and `address`. The adapter maps `full_name`, `phone_number`, `email_address`, and `raw_address` to these names. `source_system` is retained as record context and can be passed to candidate generation as `CandidateRecord.source`, never as a field similarity value.

Record/pair annotations (`entity_id`, `address_id`, `is_match`, `entity_a`, `entity_b`, `noise_type`, `difficulty`, `case_type`, `hard_negative_type`, `temporal_case`, and `split`) stay in `GoldAnnotations` or `LabeledPair.annotations`. They are not passed to candidate generation, field comparison, scoring, or routing. Candidate generation receives non-identifying candidate records and returns ephemeral row indexes; scoring receives only `FieldComparison`. IDs are used to join/trace predictions, never tokenize or similarity-score them.

## Score and result semantics

Contracts distinguish `Similarity`, `LogLikelihoodRatio`, and calibrated `Probability`. A 0–1 similarity is not automatically a probability. `PairPrediction` contains pair ID, a typed score, decision, evidence, and optional stage duration; it contains no label/entity annotation. Evaluation consumes predictions and gold through separate arguments.

Coverage gate helpers return `BLOCKED` for complete-universe pair metrics when gold coverage is sampled and for cluster metrics when complete entity labels are not independently verified. A complete-pair declaration and equal counts alone are insufficient: the gate requires exact equality of unique legal pair IDs and known labeled pair IDs. `unknown` is a separate `PairLabel` and is never coerced to negative.

## Reference-data policy

Reference inputs are local and versioned by a hash of their four loaded source files. The `.bak` file is excluded. Column aliases, administrative unit rows, administrative aliases, and old-to-new mapping entries remain separate structures. Alias collisions resolve to all candidate unit IDs. Explicit ambiguous mappings return all target candidates and evidence; the loader never selects the first candidate. Candidate identity includes target administrative version. `map_old_to_new(..., as_of=date)` does not apply a mapping before its documented effective date. Invalid mapping versions, directions, dates, targets, and referential integrity fail validation. Mapping direction stays old-to-new and is not inverted into a one-to-one mapping.
