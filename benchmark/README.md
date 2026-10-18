# MasterOne benchmark tools

From the repository root:

```powershell
python benchmark/run.py --help
python benchmark/run.py datasets list
```

The standalone CLI currently implements registry/config validation, frozen
dataset auditing, and the labeled-pair track for `exact` and `weighted`.
These scorers do not represent the production MasterOne workflow. End-to-end,
suite, ablation and comparison execution remain blocked where unimplemented.
See [standalone/README.md](standalone/README.md) for supported commands.

The former `datasets/download_febrl.py` is absent; do not use obsolete download
instructions. Frozen source hashes must match the dataset manifest. On Windows,
CRLF conversion currently makes the checked-in local test sources differ from
the manifest's LF byte hashes; this is reported as an integrity failure, not
silently bypassed.

For measurements of the production MasterOne pipeline (candidate recall, rule/embedding
evidence, TC1/TC3 end-to-end runs) and their data/label limitations, see
[docs/EVALUATION.md](../docs/EVALUATION.md). Those diagnostics use an isolated
application sandbox and do not change the standalone CLI scope.

Protocol and metric definitions: [evaluation_protocol.md](docs/evaluation_protocol.md),
[metrics_spec.md](docs/metrics_spec.md); baselines and algorithms:
[baselines_G2.md](docs/baselines_G2.md), [algorithms_G3.md](docs/algorithms_G3.md);
architecture: [architecture_G0.md](docs/architecture_G0.md); frozen dataset audit:
[dataset_audit.md](docs/dataset_audit.md).
