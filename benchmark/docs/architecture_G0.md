# Standalone benchmark architecture (G0)

`benchmark/run.py` resolves its package root from its own location and dispatches to `standalone.cli`. The new package uses only the Python standard library in the core path. It does not import `src`, `app`, web frameworks, or workflow/database code.

## Boundaries

- `standalone/contracts`, `datasets`, `references`, `algorithms`, `systems`, `baselines`, `evaluation`, and `reporting` are reserved package boundaries. G0 creates these namespaces only; later milestones must add real implementations and tests before claiming functionality.
- `standalone/config/registry.py` owns dataset, system, experiment registries and the accepted stage vocabulary. The initial `masterone_test` dataset and `masterone` system entries are registered as pending, not runnable.
- `standalone/runtime/paths.py` anchors allowed paths at the resolved benchmark directory. It rejects `..`, resolves symlinks/junctions, and denies paths whose resolved target leaves the root. `output_path()` additionally confines results to `benchmark/results/`; `cache_path()` confines temporary/cache paths to `benchmark/.cache/`.
- `standalone/runtime/metadata.py` defines seed, platform, Python/package versions, device, canonical config hash, and elapsed timing fields. No run metadata is emitted until a real run exists.

## CLI status

`datasets` displays registry status. `validate` accepts JSON and validates its schema references only. `run`, `suite`, `ablate`, and `compare` deliberately return exit code 3 with `NOT IMPLEMENTED`; they do not produce synthetic success or results.

## Dependency groups

`requirements/core.in` is empty because core currently uses only Python's standard library. `baselines.in`, `ml.in`, `splink.in`, and `extended.in` are separate optional buckets with no unverified version pins. Install/import wiring is deferred until specific implementations are selected and verified on the team's supported Windows/Python environment.

## Legacy code retained

The pre-existing `adapters/`, `metrics/`, and `runners/` remain untouched for the transition. `adapters/base.py`, `adapters/febrl.py`, `adapters/mapped_csv.py`, `metrics/core.py`, and `runners/baseline.py` import `src`; they are not part of the standalone package and are not called by `run.py`.
