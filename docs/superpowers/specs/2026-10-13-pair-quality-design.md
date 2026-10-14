# Stage 3: balanced pair quality and measured automation readiness

User authorization: continue implementation in the same staged workflow. Preserve the dirty workspace and prior stages, use the existing Compose only, test locally, leave changes uncommitted. Architectural change to assessment/review/publication and evaluation; execute inline under the existing authorization. The user confirmed a 99.5% precision target for the automatic region. This is a measured readiness target, not a raw score cutoff.

## Behavioral design

1. Keep the strict strong-anchor floor and scoring units unchanged. Add a separately versioned, deterministic **review-only** support policy after an insufficient rule conclusion. Preserve `rule_conclusion`, original comparisons/score/reason, and record why the candidate was recovered.
2. Supported evidence combinations: multi-token matching full name with an exact complete DOB; matching name with the same valid unverified person identifier (VN CCCD/CMND with an explicit namespace, or explicit identity uniqueness on both sides for custom domains); matching name plus one-edit/transposed valid non-shared phone or email plus DOB or detailed address; and exact complete DOB plus matching numbered street address (including similar administrative suffixes). Address-only, name-only, partial DOB, generic ward/province, placeholder/explicitly shared or historical contact, and arbitrary name conflicts do not qualify by themselves. Pairs with conflicting valid unverified identifiers in comparable namespaces are not recovered by this missing-anchor policy; existing anchored D pairs retain their policy. A conflicting-name DOB/address candidate stays D and requires a human reason. All recovered candidates have REVIEW triage and no automatic eligibility.
3. Hard constraints, cannot-links, explicit Deny/Keep Separate, historical assessments and decisions remain authoritative. New assessments use v4; include support implementation in assessment identity. Old sessions require explicit reassessment.
4. After explicit acceptance, publication independently checks the current support evidence for each member pair before allowing a recovered link. Do not infer permission for absent/undecided weak bridges or trust a cached support marker. Existing size/source/domain/verified conflict/reason checks remain.
5. Add bounded full-name/full-DOB and DOB/numbered-street candidate passes so high-frequency name buckets do not silently lose these cohorts. Partial/generic inputs do not generate these keys. Keep existing candidate limits and provenance; report changes and pair counts.
6. UI shows the actual supporting fields and manual-only nature, retains all detailed evidence, and uses the existing mandatory-review and cluster batching contracts.

## Evaluation and automation

- Load raw source mappings without gold. Score/generate candidates before opening CERTAIN ownership labels. Reuse stage-2 input hashes and matched record samples.
- Compute all known same-entity pairs in the loaded universe (including blocking misses), retrieval recall, candidate-level review coverage, new review precision/workload by support kind, and remaining misses by insufficient/blocked/out-of-scope.
- Run a **shadow** automatic route using only current strict MERGEABLE rule conclusions, through real component guards. Report pair and cluster-pair precision/recall and observed false merges; it never writes reviewer decisions or changes actual eligibility.
- Record machine-readable automation readiness with conservative precision confidence bounds, attempted multi-record components rather than abstaining singleton denominators, explicit labelled coverage, scope and policy/code/input identity. Empty/insufficient/missing labels and synthetic-only results cannot authorize production auto-merge. Pair confidence bounds are descriptive because pairs in the same entity/component are dependent.
- Existing data was examined during earlier stages and this exploration. Results are exploratory validation, **not an untouched holdout or production certification**. Do not train on those labels and then claim independent test accuracy. Do not repurpose the existing template-derived synthetic review model as real ownership validation.

## Validation

RED/GREEN regressions: complete versus partial DOB, supporting address versus generic address, one-digit phone typo/transposition, invalid/role/shared contacts, secondary contacts/order symmetry, unverified identifier namespace/type and placeholders, hard constraints, explicit human reason, current-input recheck versus forged support marker, absent bridge, saved reviewer decisions, recovered cluster batch/publication, candidate limits/provenance and unchanged legacy floor.

Benchmark both before/after on the same loaded raw rows, disclose TC5 structural exclusions and 12k samples from 100k cases. Run backend/frontend suites, existing Docker builds, and real browser recovery/acceptance/publication and negative scenarios. Obtain one independent final review.

## Research basis

Splink edge/cluster evaluation and labelled threshold selection: https://moj-analytical-services.github.io/splink/topic_guides/evaluation/edge_overview.html . Dedupe representative threshold evaluation versus active learning: https://docs.dedupe.io/en/latest/how-it-works/Choosing-a-good-threshold.html . NIST binomial confidence limits: https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm . These support separate workload/recall measurements and a scope-bound gate, not an arbitrary lowering of merge thresholds.
