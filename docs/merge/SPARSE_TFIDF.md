# Global sparse TF-IDF v1

2026-10-08. `CandidateConfig(sparse_tfidf=True)` enables the new path in the
same CandidateEngine used by `_compute` and StageRunner. Default remains false
per AGENT_RULES rule 9 pending representative production recall/RSS validation.
Rollback is `sparse_tfidf=False`; existing blocks, sources and serving schemas
remain unchanged. The legacy path now expands only one dense O(N) row for its
original argpartition ranking; no similarity block uses `toarray()`.

```python
from masterone.mdm.pipeline.candidate_engine import CandidateConfig
from masterone.mdm.pipeline.tfidf_blocking import SparseTfidfConfig

config = CandidateConfig(
    sparse_tfidf=True,
    tfidf=SparseTfidfConfig(fields=("name", "address"), top_k=10,
                          min_similarity=0.30, query_batch_size=64,
                          target_batch_size=256),
)
```

`GlobalTfidfIndex` ingests every snapshot record for each configured field;
empty fields count in corpus N but have no vector. NFKD/casefold strips combining
marks, maps đ to d and collapses whitespace. Character word-boundary ngrams 3–4
are the default. DOB/province/source/chunk never exclude fuzzy neighbors.
This is intentionally different from the old partitioned corpus and adaptive
max_df/min_df pruning. No vocabulary pruning or candidate-only IDF is applied.
The retrieval folding affects only vectors; stored normalized fields and scorer
semantics are unchanged.

Identical folded texts share a vector and sorted record-ID postings. Counts
are built on unique texts, but df for each gram is the sum of **record** posting
counts where the gram occurs. Smooth IDF is `log((1 + full N)/(1 + record df))+1`,
sublinear TF is `1+log(count)`, and vectors use L2 normalization/float64. Tests
compare against sklearn fit on the full repeated corpus including empty rows.

Retrieval multiplies CSR query batches against bounded sparse target slices.
Every tile is sparse, at most query_batch_size × target_batch_size cells; no
dense N×N or dense similarity batch is created. It retains K+1 text neighbors
per unique query, expands at most K+1 IDs per retained text and selects K record
neighbors excluding self. Ranking is descending cosine then ascending snapshot
ID, including ties at the cutoff. Keeping K+1 texts covers record self-exclusion;
compression does not change record Top-K. The index yields directed observations
with scores; engine canonicalizes and unions source sets `tfidf_name` and/or
`tfidf_address`. Scores are retrieval diagnostics, not a new matching score.
Union can give a record more than K undirected neighbors and more than NK/2
unique pairs; NK is the safe directed emission upper bound.

## Workload admission

Before expanding exact/composite/gram blocks the sparse engine checks all block
sizes, existing per-pass guards, eligible pair observations plus TF-IDF NK upper
bounds, estimated pair/index memory, and an aggregate snapshot/config hash.
Policy defaults: warning 20M observations, approval 40M, reject at 50M;
estimated memory budget 512 MiB. Estimates do not discount overlap. Oversized
legacy blocks keep their existing skip rules and logs rather than truncation.
Each index additionally rejects more than 100M unique-vector similarity cells
before vectorization, and more than 100M scalar sparse dot-product contributions
after vectorization but before retrieval. The latter is exactly sum(unique
feature df squared); duplicates do not inflate that computation. Both limits
are configurable `WorkloadPolicy` fields. Cell cap also bounds tile-loop overhead
for sparse disjoint corpora. No automatic change to K/threshold/batching occurs.

`WorkloadPlanError.plan` exposes ALLOW_WITH_WARNING, REQUIRE_APPROVAL or REJECT
with aggregate counts only. Approval requires retrying with
`CandidateConfig(approved_plan_id=error.plan["plan_id"])`; it is bound to the
snapshot, config and budget, and never overrides hard rejection. There is no
new UI approval flow in this task. Index standalone approval uses the same
plan mechanism; engine admission covers its sub-index pair bounds. Engine's
existing actual unique-pair cap still fails without returning partial output.

Memory is a conservative allocation estimate (ngram occurrences × 512 bytes,
record/posting allowances, bounded tile/heaps, and engine observations × 512
bytes), **not** an enforced OS RSS ceiling. Python/sklearn imports, caller-owned
snapshot, allocator behavior and other stages add memory. Full snapshot/index,
block map, candidate union and block logs still reside in RAM. Sparse tiles may
contain every cell; no fused sparse Top-K kernel or ANN speedup is claimed.

## Validation and benchmark

`scripts/run_merge_baseline.py --suite candidates` includes sparse contracts:
accent folding, typo/missing/wrong DOB, cross-province/chunk, name/address,
weighted full IDF, empty/short corpus, exhaustive record Top-K comparison with
duplicates/ties across batch sizes, determinism, forbidden CSR/CSC toarray,
bounded tile nnz, planner warning/approval/hash/reject, actual pair cap, and
StageRunner Parquet provenance/metrics equality. Baseline failures are retained.

Run `.venv/Scripts/python.exe -X utf8 scripts/benchmark_sparse_tfidf.py`.
Each distribution/size runs in a fresh child, measured with the OS lifetime peak
working-set counter on Windows (resource ru_maxrss on Unix). RSS includes imports,
synthetic input, index and CandidateEngine union; samples cannot miss short peaks.
The JSON report is `artifacts/sparse-tfidf/benchmark.json` (ignored generated file).
The committed measured summary is in STATUS.md. Seed119 fixtures are 24-letter
typo pairs, or 20-record entity groups with identical text; the first entity
uses Vietnamese variants and conflicting/missing DOB/province. Gold is every
within-entity pair. Engine benchmark disables grams/composites, keeps exact
name/contacts, uses default TF-IDF Top-K=10/threshold=.30. TF-IDF and engine recall
are reported separately. Synthetic recall is not production recall; duplicate
groups exceeding K demonstrate expected coverage loss and need union or larger K.
No comparison proving faster execution has been performed.

## Reference provenance

OneMaster working tree read only; no external runtime imports/source copies.
License not confirmed; implementation written within P-119 using existing
numpy/scipy/sklearn dependencies. Reference SHA-256:

- `candidates/tfidf.py`: `6500CA7CE896E3A4489170331E24A951306238F4A9DAE9B1CEB3271F817DCF16`
- `candidates/planner.py`: `94BE1D121EB8FEA8AEA082A19803CA2D4EDD6466D4B98174E2E6125EC96748EC`
