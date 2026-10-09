# G3 Status

## Algorithms implemented

- Normalization: `standalone/algorithms/normalization.py` — immutable raw/normalized provenance, Vietnamese accentless feature, conservative phone parsing, provider-neutral email normalization, reference-aware address evidence, and quality flags.
- Profiling: `standalone/algorithms/profiling.py` — missingness, cardinality, frequencies, shared values, source distribution, hubs, exact-block skew and explicit train/transductive-unlabeled scope; accepts G1 feature-contract inputs.
- Blocking: `standalone/algorithms/blocking.py` — exact, multi-rule union, sparse character TF-IDF top-k, and hybrid; deterministic canonical unique pairs with reasons and source legality; adapts G1 `CandidateRecord`/`IndexPair` interface.
- Fellegi–Sunter: `standalone/algorithms/fellegi_sunter.py` — add-alpha m/u estimation, log-space `LogOdds`, field levels, hard-conflict signals, quality policy, optional TF/hub adjustment, deterministic trace, and a `PairScorer` contract adapter.
- Calibration/routing: `standalone/algorithms/calibration.py`, `standalone/algorithms/routing.py` — DEV-only Platt calibration and DEV-sourced two-threshold three-way routing; threshold router implements the G1 routing contract.
- Temporal/ablations: `standalone/algorithms/temporal.py`, `standalone/algorithms/ablations.py` — missing timestamps remain unknown; A0–A6 catalog marks A6 unavailable without business timestamps.
- Splink: `standalone/baselines/splink.py`, `requirements/splink.in` — lazy optional dependency, pinned requirement, native-output normalization, capability flags; fixed-pair scoring explicitly unsupported.
- Formula and scope decisions: `docs/algorithms_G3.md`.

No advanced G4 cluster safety or G3 test-set result was implemented.

## Tests

- Command: `python -m pytest -p no:cacheprovider --basetemp=.cache\pytest-g3 tests`
- Initial run: 66 passed, 4 failed. Failures exposed wrong normalizer method names in fixtures, unstable Platt fitting on separable data, and reversed routing threshold selection. Fixes were applied.
- Regression run: **72 passed** (G0–G3 and integrity review tests).
- Coverage includes normalization idempotence/Vietnamese text/missing phone/email/address ambiguity; blockers, dedupe and multi-source legality, uniqueness and determinism; hand-calculated FS score, smoothing, conflicts, numerical stability and train-only fit; rare/common TF and hub controls; DEV-only calibration and routing boundary/conflict policies; protocol adapters; profiler scope; unknown temporal data; Splink optional behavior and capability; ablation availability.

## Commands

- Regression command above; pytest temp path is under `benchmark/.cache`.
- CLI smoke: from repo root `python benchmark/run.py --help` — PASS; from `benchmark/` `python run.py --help` — PASS.
- Optional dependency availability: `SplinkAdapter.availability()` returns the installed state without importing Splink during core module import.
- G0/G1/G2 CLI and test coverage is included in the full regression command. No learned/calibrated benchmark CLI result is claimed because the dataset has no train/dev split.

## Evidence

- `tests/test_g3.py`
- `standalone/algorithms/normalization.py`
- `standalone/algorithms/profiling.py`
- `standalone/algorithms/blocking.py`
- `standalone/algorithms/fellegi_sunter.py`
- `standalone/algorithms/calibration.py`
- `standalone/algorithms/routing.py`
- `standalone/algorithms/temporal.py`
- `standalone/algorithms/ablations.py`
- `standalone/baselines/splink.py`
- `docs/algorithms_G3.md`
- `.cache/pytest-g3` (test scratch, benchmark-local)

## Blocked

- The MasterOne dataset currently contains test-only labeled pairs. Supervised FS fitting on the real dataset, calibration, and threshold selection remain **BLOCKED**; only hand-built contract fixtures were fitted.
- Splink is absent from the active environment. Optional adapter behavior is tested; a real Splink integration smoke is **SKIPPED/BLOCKED**. Install only from `requirements/splink.in` to run it.
- The source checklist `docs/BENCHMARK_IMPLEMENTATION_CHECKLIST.md` is 0 bytes in this workspace. It was not reconstructed or marked; task IDs and checkbox evidence cannot be safely synchronized until the source checklist is restored.
- The G0/G1 review verdict remains `NOT_READY_FOR_G2` due missing valid train/dev data; G2 technical tests pass but its formal checklist handoff remains blocked. G3 algorithm fixtures do not override those dataset gates.

## Evidence and gate decision

**Algorithm unit/regression gate: PASS. Overall G3 gate: FAIL/BLOCKED.** Code and regression evidence exist, but no valid train/dev data supports real fit/calibration/threshold selection; Splink is unavailable; the authoritative checklist is empty; and prior stage handoff blockers remain unresolved. No test labels were used for tuning or claims.
