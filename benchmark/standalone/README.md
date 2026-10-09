# Standalone benchmark system

This package is the new benchmark-owned implementation boundary. It must remain independent from `src/`, `app/`, FastAPI, and workflow/database code. The G0 package layout and path/dependency decisions are documented in [architecture_G0.md](../docs/architecture_G0.md).

## Current commands

```powershell
python benchmark/run.py --help
python benchmark/run.py datasets list
python benchmark/run.py datasets audit --dataset masterone_test
python benchmark/run.py validate --config standalone/config/experiments/example.json
python run.py validate --config standalone/config/experiments/example.json
python benchmark/run.py run --dataset masterone_test --systems exact,weighted --track pairs
python benchmark/scripts/audit_g1_data.py
```

`datasets list` shows registered dataset/system status. `datasets audit` reads and validates the frozen internal MasterOne files without writing artifacts. `validate` checks experiment JSON syntax, known keys, stage, and registry names. `run --track pairs` evaluates the implemented **B0 exact/rule** and **Weighted V1 reimplementation** on the listed labeled test pairs. Results are descriptive internal test scores, not an untouched holdout; thresholds are fixed in the baseline version and are not tuned on this set. Unlisted legal pairs remain unknown. `--track end-to-end`, `suite`, `ablate`, and `compare` fail explicitly until implemented. Terminal runs do not export results or predictions.

The `MasterOneAdapter` requires the frozen dataset manifest by default. It returns records separately from pair/entity annotations; `build_prediction_inputs()` strips gold, splits, annotations, and IDs from stage-facing inputs while retaining a separate trace map for evaluation. See [evaluation_protocol.md](../docs/evaluation_protocol.md); train/dev work is blocked until entity-disjoint labels are available.

Paths are resolved relative to the package location, independent of current working directory. Result paths belong under `benchmark/results/`, cache and temporary paths under `benchmark/.cache/`. The runtime guard rejects traversal and resolved symlink/junction targets outside the benchmark root.

## Legacy transition

`benchmark/adapters`, `benchmark/metrics`, and `benchmark/runners` are retained legacy code. They import application modules from `src/` and are not used by the standalone entry point. Keep them until a later migration explicitly checks compatibility.
