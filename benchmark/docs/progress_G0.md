# G0 Status

G0 — Scope & Architecture: **PASS** (2026-09-30). This gate covers only the independent package/path foundation and honest CLI skeleton. G1 and later work has not started.

## Implemented

- SCOPE-01 — All changes made for this task are below `benchmark/`; application code, root entry points, and root configuration were not edited. Evidence: repository `git status --short --untracked-files=all` audit; all reported paths were prefixed `benchmark/`.
- SCOPE-04, ARCH-03 — Added `run.py` with package-root resolution anchored to its own file location. Evidence: `tests/test_g0.py::test_help_is_cwd_independent` invokes both `python benchmark/run.py --help` from the repository root and `python run.py --help` from `benchmark/`, plus a third working directory.
- SCOPE-06 — Standalone entry/import path has no app, web, or database integration. Evidence: `tests/test_g0.py::test_standalone_import_does_not_import_application`.
- SCOPE-08 — Added resolved-root path guard, traversal rejection, and symlink/junction target validation; results and cache helpers target `results/` and `.cache/`. Evidence: `tests/test_g0.py::test_path_guard_rejects_traversal_and_outside_paths`, `test_path_guard_resolves_symlink_escape`.
- ARCH-01 — Added package namespace boundaries separately from source data. Raw data remains under `datasets/`; reader code boundary is `standalone/datasets/`. Evidence: `docs/architecture_G0.md`.
- ARCH-04 — Added dataset/system/experiment registries, known stage names, strict JSON object/schema checks, and clear failures for unknown stage, system, dataset, experiment, and stage parameter. Evidence: `standalone/config/registry.py`; `tests/test_g0.py::test_config_validation_reports_malformed_and_unknown_system`.
- ARCH-05 — Core uses the standard library; baseline, ML, Splink, and extended dependency groups are separated. Evidence: `requirements/*.in`; `tests/test_g0.py::test_optional_dependencies_are_not_needed_for_core_help` blocks optional imports while invoking core help.
- ARCH-07 — Updated both READMEs with commands that exist and status of retained legacy code. Evidence: `README.md`, `standalone/README.md`.
- Runtime metadata contract — Added seed, platform, Python version, package versions, device, config hash, and timing fields. Evidence: `standalone/runtime/metadata.py`.
- Honest CLI skeleton — `datasets` shows pending registry entries; `validate` validates JSON and makes no run claim. `run`, `suite`, `ablate`, and `compare` return exit code 3 with `NOT IMPLEMENTED`, without creating results. Evidence: `standalone/cli.py`; `tests/test_g0.py::test_unimplemented_command_is_explicit_and_nonzero`.

## Tests executed

- `python -m pytest -p no:cacheprovider --basetemp=.cache/pytest-tmp -rs tests/test_g0.py` — **14 passed**, Windows platform, Python 3.11.9. Includes root/benchmark/other-cwd CLI, app import isolation, data/code separation, README command accuracy, dataset status, outside-root and traversal rejection (including output helper), junction escape rejection, malformed stage/system/parameter config, Unicode workspace path, example config, and optional dependency import blocking.
- The test run sets `TMP`, `TEMP`, `TMPDIR`, and pytest `--basetemp` under `benchmark/.cache`; bytecode and pytest cache are disabled.

## Evidence

- Tests: `tests/test_g0.py`.
- Architecture and decisions: `docs/architecture_G0.md`.
- CLI: `run.py`, `standalone/cli.py`.
- Path/runtime contracts: `standalone/runtime/paths.py`, `standalone/runtime/metadata.py`.
- Registries: `standalone/config/registry.py`.
- Example config: `standalone/config/experiments/example.json`.
- Dependency groups: `requirements/core.in`, `baselines.in`, `ml.in`, `splink.in`, `extended.in`.
- Test scratch files are confined to `.cache/pytest-tmp`; test-created config and junction are removed by the test.

## Repository audit

- Read in full before implementation: `docs/BENCHMARK_IMPLEMENTATION_CHECKLIST.md`, `README.md`, `standalone/README.md`, and `docs/deep-research-report.md`.
- Existing legacy benchmark code retained: `adapters/base.py`, `adapters/febrl.py`, `adapters/mapped_csv.py`, `metrics/core.py`, `runners/baseline.py` (plus their `__init__.py` files). `adapters/base.py`, `adapters/febrl.py`, `adapters/mapped_csv.py`, and `metrics/core.py` import `src.models.masterone`; `runners/baseline.py` imports `src.models.masterone` and `src.pipeline.*`. No standalone file imports `src/` or `app/`.
- Existing data retained: `datasets/masterone_test/{records.csv,pairs.csv}`, the four address-reference CSV/JSON sources and `.bak`, and `datasets/sources.json`. The pre-existing `results/.gitkeep` is retained.
- Existing standalone scaffold retained: `algorithms/`, `config/`, `data/`, `datasets/`, `evaluation/`, `results/`, and `standalone/README.md`. New package boundaries/config/runtime/CLI were added alongside it. `standalone/data/` and `standalone/results/` had no tracked files in the initial inventory and were not deleted.
- `datasets/download_febrl.py` was absent from the initial file inventory, although Git reports it as a pre-existing deletion. It was not restored or modified during G0. Existing README references to it remain legacy documentation and should be reconciled in a later acquisition task.
- Not implemented: dataset loading/preparation, reference lookup, record/pair contracts, algorithms, baseline adapters, evaluation metrics, suite execution, ablation execution, comparison, and real result export. The G0 namespaces are architecture boundaries only.
- After tests, Git status from the repository root contained no paths outside `benchmark/`; `.cache/` contained only its `.gitkeep`, and the junction/config test artifacts had been cleaned up.

## Failed / Blocked

- None for the G0 gate. Junction escape test passed using a Windows directory junction after symlink creation was unavailable.
- Dependency versions are not pinned: no non-core package has been selected or validated for the group's Windows/Python environment. This is recorded as a remaining architecture task, not silently treated as complete.

## Technical decisions

- Keep all new benchmark code under `standalone/`; no application imports and no root `benchmark.py` changes.
- Use JSON only for G0 config validation to avoid adding a YAML dependency before a real protocol schema exists.
- Keep registries explicit and initial dataset/system entries marked pending. The experiment registry is empty until an experiment is actually defined.
- Do not implement result generation, data loading, algorithms, or scores in G0. Execution commands fail visibly until later gates provide real behavior.
- Keep the existing `adapters/`, `metrics/`, `runners/`, `standalone/data/`, and `standalone/results/` scaffolds. Existing legacy code is not deleted as part of this foundation.
- Output and cache helper paths are anchored under `results/` and `.cache/`. The path guard rejects any `..` component and validates resolved symlink/junction targets.
- `RuntimeMetadata` is a contract only; it does not claim that an experiment has run or that any metrics/results exist.

## Remaining risks

- `ARCH-02` is not complete: legacy `standalone/results/` is retained pending an explicit compatibility/cleanup step; new exports must use `benchmark/results/`.
- `ARCH-06` is not complete: packages and versions must be selected and verified on the team's supported platform before producing lock files.
- Dataset readers, contracts, algorithms, baseline adapters, evaluators, report output, and actual experiment configs remain unimplemented. These belong to later gates.
- Network and subprocess write policies for future acquisition/baseline tools are not exercised by G0.
- `datasets/download_febrl.py` was absent in the initial workspace audit and remains untouched; the README's legacy reference must be reconciled in a later dataset/acquisition task.

## Gate decision

**PASS** — standalone package/path boundary, CLI skeleton, path isolation, optional dependency isolation, and required G0 tests are in place. No G1+ functionality or benchmark result is claimed.

## Integrity review addendum (2026-09-30)

The G0/G1 integrity review found that `output_path()` and `cache_path()` rejected paths outside `benchmark/` but accepted absolute paths elsewhere inside it. The helpers now enforce their designated `results/` and `.cache/` scopes. The G0 foundation gate remains PASS after regression testing. The checklist's ARCH-04 and ARCH-05 checkboxes were reopened: config/registry and dependency group foundations exist, while real algorithm entries, executable experiment registration, and lazy optional adapter wiring are pending. Evidence: `tests/test_integrity_review.py`, `docs/SOL_REVIEW_G0_G1.md`.
