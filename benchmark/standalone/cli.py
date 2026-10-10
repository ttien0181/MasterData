"""Standalone benchmark CLI; only implemented tracks are advertised as runnable."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from standalone.config.registry import (
    EXPERIMENTS,
    SYSTEMS,
    STAGES,
    ConfigError,
    validate_experiment_config,
)
from standalone.runtime.paths import PathSafetyError, benchmark_root

NOT_IMPLEMENTED = {
    "suite": "suite execution",
    "ablate": "ablation execution",
    "compare": "run comparison",
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="benchmark",
        description="Standalone MasterOne entity resolution benchmark.",
        epilog=("G0 commands: datasets (registry status) and validate (JSON config). "
                "Implemented run scope: internal MasterOne labeled pair evaluation with exact and weighted baselines."),
    )
    sub = parser.add_subparsers(dest="command", required=True)
    datasets = sub.add_parser("datasets", help="list registered datasets or audit a dataset")
    dataset_commands = datasets.add_subparsers(dest="dataset_command")
    dataset_commands.add_parser("list", help="list dataset and system status")
    audit = dataset_commands.add_parser("audit", help="audit the frozen internal MasterOne source data")
    audit.add_argument("--dataset", required=True)
    validate = sub.add_parser("validate", help="validate a standalone experiment JSON config")
    validate.add_argument("--config", required=True, help="JSON config path inside benchmark/")
    run = sub.add_parser("run", help="run supported pair baselines")
    run.add_argument("--dataset", required=True)
    run.add_argument("--systems", required=True, help="comma-separated: exact,weighted")
    run.add_argument("--track", required=True, choices=("pairs", "end-to-end"))
    for name in ("suite", "ablate"):
        cmd = sub.add_parser(name, help=f"{name} execution (not implemented)")
        cmd.add_argument("--config", help="experiment JSON config")
    compare = sub.add_parser("compare", help="compare runs (not implemented in G0)")
    compare.add_argument("--before", help="first run directory")
    compare.add_argument("--after", help="second run directory")
    return parser


def _datasets_status() -> int:
    print("Dataset registry:")
    if not EXPERIMENTS.datasets:
        print("  (empty)")
    for name, entry in sorted(EXPERIMENTS.datasets.items()):
        print(f"  {name}: {entry.status}")
    print("System registry:")
    for name, entry in sorted(SYSTEMS.items()):
        print(f"  {name}: {entry.status}")
    print("Experiment stages:")
    print("  " + ", ".join(sorted(STAGES)))
    return 0


def _dataset_audit(dataset: str) -> int:
    if dataset != "masterone_test":
        print(f"BLOCKED: dataset {dataset!r} has no implemented adapter/audit.", file=sys.stderr)
        return 3
    from standalone.datasets.audit import audit_masterone
    from standalone.datasets.masterone import MasterOneAdapter
    root = benchmark_root()
    directory = root / "datasets" / dataset
    audit = audit_masterone(directory / "records.csv", directory / "pairs.csv")
    if not audit.structurally_valid:
        print("AUDIT FAILED: structural defects were found.", file=sys.stderr)
        return 2
    MasterOneAdapter(directory).load()  # verify locked config, source schema, hashes, and manifest
    print(f"Dataset audit: {dataset}@{audit.dataset_version}")
    print(f"Records: {audit.records.row_count}; labeled pairs: {audit.pairs.row_count}")
    print(f"Pair labels: {audit.pair_label_distribution}; splits: {audit.pair_split_distribution}")
    print(f"Structural validity: {audit.structurally_valid}; sources: {audit.source_distribution}")
    print("Frozen dataset manifest: verified.")
    print("Gold caveat: listed labels are sampled; unlisted legal pairs remain unknown.")
    return 0


def _run(dataset: str, systems_arg: str, track: str) -> int:
    if dataset != "masterone_test":
        print(f"BLOCKED: dataset {dataset!r} has no implemented runner.", file=sys.stderr)
        return 3
    if track != "pairs":
        print("BLOCKED: track 'end-to-end' requires implemented normalization, candidate generation, "
              "and clustering; no result was produced.", file=sys.stderr)
        return 3
    systems = tuple(item.strip() for item in systems_arg.split(",") if item.strip())
    if len(systems) != len(set(systems)):
        print("CONFIG ERROR: duplicate system names in --systems.", file=sys.stderr)
        return 2
    try:
        from standalone.evaluation.runner import format_terminal_report, run_masterone_pair_baselines
        print(f"Loading {dataset} and running pair track for {', '.join(systems)}...", file=sys.stderr)
        result = run_masterone_pair_baselines(systems)
        print(format_terminal_report(result))
    except (OSError, ValueError, KeyError) as exc:
        print(f"RUN ERROR: {str(exc).encode('ascii', 'backslashreplace').decode('ascii')}", file=sys.stderr)
        return 2
    return 0


def _validate(path_arg: str) -> int:
    try:
        root = benchmark_root()
        path = Path(path_arg)
        if not path.is_absolute():
            path = root / path
        path = root_guard(path)
        with path.open("r", encoding="utf-8") as handle:
            config: Any = json.load(handle)
        validate_experiment_config(config)
    except (PathSafetyError, OSError, json.JSONDecodeError, ConfigError) as exc:
        safe_error = str(exc).encode("ascii", "backslashreplace").decode("ascii")
        print(f"CONFIG ERROR: {safe_error}", file=sys.stderr)
        return 2
    print("Config schema and registry references are valid.")
    print("Execution was not performed (G0 validate only).")
    return 0


def root_guard(path: Path) -> Path:
    from standalone.runtime.paths import resolve_benchmark_path
    return resolve_benchmark_path(path)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "datasets":
        if args.dataset_command == "audit":
            return _dataset_audit(args.dataset)
        return _datasets_status()
    if args.command == "validate":
        return _validate(args.config)
    if args.command == "run":
        return _run(args.dataset, args.systems, args.track)
    label = NOT_IMPLEMENTED.get(args.command)
    if label:
        print(f"NOT IMPLEMENTED: {label}; no benchmark was run.", file=sys.stderr)
        return 3
    return 2
