# MDM Pipeline Local UI Design

**Date:** 2026-09-25  
**Status:** Brainstorming design, ready for user review  
**Scope:** Local UI for running one MDM deduplication dataset and reviewing benchmark results.

## Goal

Provide a local browser interface to select one supported dataset, run the existing MDM pipeline, follow its progress, and review current and historical benchmark metrics. The first version supports one dataset per run and the currently registered datasets (`febrl3` and `febrl4`).

## Chosen approach

Use Streamlit as a local Python UI. It can call the existing pipeline directly and display tables, charts, stage progress, and logs without adding a separate frontend service. Keep the current command-line entry point available. The existing FastAPI AI-agent sample in `src/` is not part of this UI.

## User flow and screens

The app has two areas on one page:

### Run pipeline

1. Select one dataset from `DatasetFactory.list_available_datasets()`.
2. Start the run. Disable another start action while a run is active.
3. Show the active stage, a progress indicator, and recent pipeline log lines as the run proceeds.
4. On success, show Precision, Recall, F1, Reduction Ratio, total elapsed time, and per-stage timings.
5. Keep the full run log available in an expandable section for diagnosis.

Pipeline algorithm settings remain sourced from the current project configuration. The first version does not expose threshold or model tuning controls and does not offer a batch run.

### Benchmark history

Read `benchmark/results/benchmark_results_*.csv` as the historical source. Combine rows across daily files, show dataset and timestamp, and allow filtering by dataset and sorting by timestamp. Plot F1 by run over time. Provide a clear empty state when no results exist. The UI does not edit or delete historical rows.

## Architecture and data flow

- Add a Streamlit entry point under `ui/`; launch it locally with Streamlit.
- The UI calls an application-facing pipeline interface in-process. It does not shell out to `main.py` and does not call the unrelated FastAPI sample.
- Extend the pipeline run interface to return a structured result containing the selected dataset, benchmark metrics when available, total duration, per-stage durations, and final status.
- Add an optional progress/log observer at pipeline stage boundaries. Preserve the existing Rich console output for CLI users while also emitting stage and log events for the UI.
- Reuse the existing `CSVReporter` as the single writer of successful benchmark rows. The UI reads those CSV files after a run and must not append a duplicate row.
- A ground-truth dataset produces benchmark metrics and a history entry. If a future supported dataset lacks ground truth, show a completed run without benchmark metrics and explain why it is absent.

## Run state and error handling

- Permit only one pipeline run at a time per local app process, since the pipeline is resource-intensive and results share the same local output directory.
- Represent stages as pending, running, completed, or failed. Do not present a failed stage as completed.
- Catch run failures at the UI boundary, mark the run failed, and show a concise error with the last stage. Keep captured logs and the traceback in an expandable diagnostic section.
- Do not fabricate or persist benchmark metrics for an incomplete run. The reporter remains responsible for writing only when benchmark evaluation succeeds.
- If a history file cannot be read or has malformed rows, show a warning and continue displaying valid rows from other files where possible.
- If no history files or rows are available, show the empty state instead of an error.

## Acceptance criteria

- The app starts locally and lists exactly the datasets currently exposed by the dataset factory.
- A user can start one selected dataset run and see stage progress and logs without starting a second run concurrently.
- A successful benchmark run displays its metrics and timing, and adds exactly one row through the existing CSV reporter.
- History combines dated CSV files, supports filtering by dataset, and shows F1 trends in timestamp order.
- A pipeline error is visible as a failed run with diagnostic logs; it does not show success metrics for stages or benchmark work that did not complete.
- The existing CLI entry point and its behavior remain available.

## Validation strategy

During implementation, validate the result interface and event sequence against the current pipeline stages, exercise successful FEBRL3/FEBRL4 runs where local data and dependencies are available, check history loading with multiple daily CSV files and empty/malformed inputs, and perform a local Streamlit browser smoke check. Do not require network access for ordinary UI startup or for loading existing cached datasets.

## Out of scope

- Hosted or multi-user deployment, authentication, or remote job execution.
- Batch execution of multiple datasets.
- Editing pipeline algorithm settings from the UI.
- Reviewing or editing individual matched record pairs.
- Replacing the CLI or repurposing `src/` FastAPI routes for MDM pipeline operations.
