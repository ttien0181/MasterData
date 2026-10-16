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

For the production-pipeline diagnostic E0 and its data/label limitations, see
[M0 protocol](../docs/advanced-matching/evaluation-protocol.md) and
[E0 report](../docs/advanced-matching/baseline-e0.md). These diagnostics use a
fresh isolated application sandbox and do not change the standalone CLI scope.
