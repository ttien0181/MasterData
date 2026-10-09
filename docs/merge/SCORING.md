# Scoring contract v1

2026-10-08. `ScoringEngine`/`ScoreResult` in
`backend/src/masterone/mdm/pipeline/scoring_engine.py` are shared by small
`resolution_service._compute`, large `StageRunner.score_candidates`/`run_all`
and `run_pipeline(scoring_config=...)`. Rule mode remains the default. The
import-Splink-then-pass branch was removed. CandidateEngine/provenance and pair
ID SHA-256 remain unchanged. No frontend, DB schema, training or calibration
changes are made by this task.

Subsequent decision task adds a pinned person-semantics opt-in and shared
review/persistence adapter; see [DECISIONS](DECISIONS.md). Historical scoring
claims below describe the original scoring task, not decision rollout readiness.

The remaining `score_pair` call in `resolution_items` reconstructs missing
display evidence for historical no-merge items. It uses the compatibility
facade and is outside pipeline SCORE; it does not rewrite persisted scores or
decisions and was not changed in this task.

## SCORE and DECIDE

SCORE produces **one row for every unique canonical candidate pair**, including
hard-negative/conflicting and insufficient-evidence pairs. Evidence comparison
and baseline numerical weights are produced before decision. Existing
`matching.score_pair` remains a compatibility facade over
`compare_and_score_rule` then `decide_rule_result`. Existing field state,
shared-contact threshold, subgroup/floor and merge-policy semantics are retained.
Policy thresholds evaluate the original unrounded weight, preserving behavior
at numerical boundaries.

| SCORE column | Meaning |
| --- | --- |
| left_id / right_id | Canonical snapshot IDs; self/reversed/missing IDs rejected |
| blocking_reasons | Sorted complete candidate provenance |
| rule_score | Existing rule weight rounded to 6 decimals; **not probability** |
| raw_rule_score | Unrounded baseline weight for existing policy/floor checks |
| match_probability | Splink model output in [0,1], NULL in rule mode |
| splink_match_weight | Splink log-odds weight, NULL in rule mode |
| evidence_json | Field comparisons, evidence, signature/tags and hard constraint facts; no score or decision |
| model_version / model_hash / config_hash | Fixed scoring identity |

DECIDE consumes SCORE in batches and applies the frozen copy of existing rule
policies. It drops insufficient-evidence pairs only from serving/cluster output,
counts them explicitly and preserves them in the SCORE artifact. Existing
decision `score`/`match_weight` aliases retain their rule-weight units. Splink
probabilities are additional shadow outputs; there is **no probability-based
auto-merge policy or arbitrary threshold**. Semantic address enrichment remains
after rule decision and can change serving score, as before; `rule_score` records
the original baseline. Numerical Splink output never overrides hard negatives
or evidence/policy. Activating a future probability decision policy requires its
own evaluation/contract task.

## Full snapshot and frozen run identity

Both paths perform the same census over all normalized records, even when a
record is never a candidate endpoint. Frequency semantics are unchanged from
the small baseline: phone/email scalar primary values, nonempty, one count per
record including repeats, regardless of field quality; no expansion to secondary
contacts. This intentionally fixes the large path's candidate-only census.
Splink input also contains the complete snapshot; native TF adjustments use
that corpus, never the per-batch hydrated records.

`ScoringConfig` is frozen and hashed at engine construction; default batch size
2048, rule version `p119_rule_v1`, normalization shape `p119_normalized_v1`.
The normalization tag declares the existing P-119 normalized-field shape; it
does not backfill historical record normalization metadata. Rule model hash
covers matching, standardization, hard constraints, floor and policy source.
Metadata includes loaded policy hash, negative-pair hash, full snapshot hash,
frequency hash/count, package/Python versions, config and artifact/content hashes.
Policies and loaded Splink settings/manifest are copied once for the run. Full
snapshot census/source hashes are rechecked before finalizing the artifact.
An existing artifact with a different model/config/snapshot/policy/runtime
identity or changed bytes cannot be overwritten; use a fresh run artifact.

## Batching, coverage and artifacts

SCORE hydrates at most 2 × batch_size endpoint records and holds only one batch
of ScoreResults. Rule output uses a typed ParquetWriter, including empty runs.
SQLite uniqueness ledger on disk detects duplicate input pairs across batches.
Per-batch output ID-set/count checks detect missing, duplicate or substituted
predictions even if total counts agree. Final count and Parquet metadata must
equal input count. No threshold filters run in SCORE. On failure, partial files
and ledger are deleted; a prior completed artifact is preserved.

Artifacts are written to a sibling temporary Parquet, validated, then locally
replaced; `.score.json` holds pinned identity, coverage and artifact hash. This
is a local scoring artifact contract, **not** a complete durable publication or
multi-file transaction protocol. The sidecar is written after replacement;
interrupted output/sidecar pairs are detected by the next invocation's hash
check. StageResult exposes the same metrics; StageResults are not newly persisted
to DB in this task. Existing human review/golden/audit persistence is untouched.

`_compute(scoring_output_dir=...)` supports explicit artifact storage; without
it, a unique `p119-scores-*` OS temporary directory is retained for the artifact.
Return keys `scoring_artifact`/`scoring_metrics` identify it. Caller/deployment
must manage retention. The isolated test runner redirects TEMP/TMP/TMPDIR into
its own disposable directory, so tests leave no score artifacts elsewhere.
StageRunner writes its SCORE artifact in its existing output directory.

This does not make the entire pipeline bounded-memory: full frequency Counters,
small input records, CandidateEngine blocks/union, DECIDE decision rows and
CLUSTER records/proposals still use memory. SCORE's endpoint hydration and
numeric outputs are bounded by batch size; global census cardinality is not.
The old SCORE list of all `scored_pairs` and all-candidate record cache is gone.
Downstream DECIDE still materializes decision rows for existing semantic/cluster
consumers and preserves their ordering/limits. No production RSS claim is made.

## Splink readiness blocker and adapter

No compatible **trained, calibrated and evaluated P-119 model** was found.
Inventory of model/manifest JSON in P-119 `benchmark`, `eval`, `artifacts`
(including ignored files) returned none. OneMaster performance artifacts exist;
inspected manifests use `vn_normalization_v1`, record unestimated parameters
and explicitly warn scores are not calibrated. They are not imported or used
as a P-119 production model. **Splink mode remains inactive**; requesting it
without complete valid artifacts raises `SplinkModelBlockedError`, including
empty runs. No fallback to rule or automatic training occurs.

To enable only after external training/calibration/evaluation, supply
`ScoringConfig(mode="splink", model_path=..., manifest_path=...,
model_version=..., model_sha256=...)`. Validation requires actual model SHA-256
to match config/manifest, matching normalization/version/fields, literal
`trained`, `calibrated`, `production_approved` booleans true, training snapshot
and evaluation-report SHA-256 references, complete finite fitted m/u
distributions and prior. The supported artifact has `dedupe_only`,
`unique_id_column_name="record_id"`, and direct normalized name/phone/email/
address/dob/government_id projection. This adapter supports fitted m/u strictly
inside (0,1), summing to 1; unsupported artifacts fail rather than substitute
defaults. Manifest assertions are caller-provided evaluation attestation; the
runtime does not perform or certify calibration itself.

The adapter pins Splink **5.0.0** (installed baseline version), feeds the full
snapshot into a scratch DuckDB file, defaults to 256 MiB DuckDB memory limit
and one thread, and uses the public
[registered blocked-pair API](https://moj-analytical-services.github.io/splink/api_docs/table_management.html#register_blocked_pairs_for_predict)
with `predict()` on each candidate batch. No training calls or probability
threshold are used. A fresh Linker per batch prevents stale prediction-cache
reuse; transient prediction tables/views are cleaned up. Registered-pair
coverage and finite numeric outputs are validated. DuckDB's memory setting is
not a whole-process RSS ceiling.

Tests run the **real Splink inference API** using explicitly uncalibrated fixed
arithmetic fixtures, never a trained or production model. One test bypasses
readiness only with pytest monkeypatch to exercise orchestrated batches/native
full-corpus TF/frozen settings; the production readiness guard remains required.
These tests prove API integration/coverage, not calibrated production accuracy.

## References

OneMaster matching/candidate_scoring.py and splink_adapter.py read only; no
external runtime imports, source copies or new dependency. License not confirmed.
SHA-256 at reading:

- candidate_scoring.py: `5165FACF150C252E0A25970637D01C104A43298F36841FFD6519C172FCC3D242`
- splink_adapter.py: `22EFC00D5F8EDE3E46C853E4B3E804A2369934EA752E8937D358BA604F467419`

Validation commands/results and remaining baseline failures: [STATUS](STATUS.md).
Next: provide an evaluated P-119 normalization-compatible frozen Splink model
and manifest, then shadow evaluation. Probability-policy activation, external
census storage, downstream streaming and durable publication remain separate.
