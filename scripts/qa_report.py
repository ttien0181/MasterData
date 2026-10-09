"""Assemble evidence index and measured QA reports; never infer unexecuted PASS."""
import json
import os
import platform
import xml.etree.ElementTree as ET
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "artifacts/full-system-20261009"
DOC = ROOT / "docs/qa"
DOC.mkdir(parents=True, exist_ok=True)


def read(relative):
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def link(relative):
    return f"[{relative}](../../{relative})"


matrix = []


def row(name, expected, actual, status, evidence, command=""):
    assert status in {"PASS", "FAIL", "BLOCKED", "NOT RUN"}
    assert not evidence or (ROOT / evidence).exists()
    matrix.append(dict(test=name, expected=expected, actual=actual, status=status,
                       evidence=evidence, command=command))


def cases(relative):
    payload = read(relative)
    return payload["cases"] if isinstance(payload, dict) else payload


def passed(relative):
    data = cases(relative)
    assert data and all(item["status"] == "PASS" for item in data), relative
    return len(data)


full = "artifacts/merge-baseline/20261009T045434Z-j07fsl5t/pytest.xml"
root = ET.parse(ROOT / full).getroot()
tests = list(root.iter("testcase"))
assert sum(t.find("failure") is not None or t.find("error") is not None for t in tests) == 0
skips = sum(t.find("skipped") is not None for t in tests)
assert len(tests) - skips == 884 and skips == 1
row("Backend original baseline", "No failing regression", "846 passed,11 failed,1 skipped; historical failures retained", "FAIL", "artifacts/merge-baseline/20261009T025648Z-0770z2tc/pytest.xml", "scripts/run_merge_baseline.py --suite all --timeout900")
row("Backend final full regression", "All active tests pass; skips disclosed", "884 PASS,1 SKIP,469.10s,exit0; before final explanation-schema change", "PASS", full, "scripts/run_merge_baseline.py --suite all --timeout900")
row("Removed partition-key function test", "Legacy function coverage", "Explicitly skipped because internal function was removed", "NOT RUN", full)
target = "artifacts/full-system-20261009/gemini-schema-final/results.xml"
assert len(list(ET.parse(ROOT / target).getroot().iter("testcase"))) == 43
row("Post-schema related regression", "Explanation/schema/API regressions pass", "43 PASS,9.67s; real Gemini verification separately", "PASS", target, "scripts/qa_run_tests.py --output ... test_qa_regressions.py test_document_pipeline.py test_selective_integration.py")
row("Frontend unit", "All Vitest assertions pass", "50 PASS,10files; XML runtime7.60s,wall execution recorded earlier", "PASS", "artifacts/full-system-20261009/frontend-baseline.xml", "frontend: npm test -- --reporter=junit")
typecheck = read("artifacts/full-system-20261009/frontend-typecheck-final/results.json")
assert typecheck["exit_code"] == 0 and typecheck["status"] == "PASS"
row("Frontend typecheck/lint script", "tsc --noEmit exit0", f"Re-executed exit0,{typecheck['seconds']}s; npm lint is TypeScript check, not ESLint", "PASS", "artifacts/full-system-20261009/frontend-typecheck-final/results.json", "frontend: npm run lint")
mock = read("artifacts/merge-validation/frontend-1791517010820/results.json")
assert mock["stats"]["expected"] == 10 and mock["stats"]["unexpected"] == 0
row("Existing mocked Playwright", "10 expected specs pass", "10 PASS; Next dev + Edge, API mocked", "PASS", "artifacts/merge-validation/frontend-1791517010820/results.json", "node scripts/run_merge_frontend_smoke.mjs")
runtime = read("artifacts/full-system-20261009/final-runtime/results.json")
assert runtime["status"] == "PASS" and all(v["equal"] for v in runtime["source_image_hashes"].values())
row("Docker build/config/source parity", "Build/ready and installed source matches", "Build exit0;13module hashes equal;5services running,3healthchecks healthy", "PASS", "artifacts/full-system-20261009/final-runtime/results.json", "scripts/qa_final_snapshot.py")
smoke_path = "artifacts/full-system-20261009/live-image-final/results.json"
row("Real local smoke", "Auth/negative imports/100,1k,4k/export/S3 pass", f"{passed(smoke_path)} PASS cases on final installed image", "PASS", smoke_path, "scripts/qa_live_system.py --mode smoke --output ...")
row("Invalid CSV/auth/CSRF/CORS", "Reject empty/schema/duplicate/malformed/encoding/name errors atomically", "6 invalid inputs422; badCSRF403,evilOrigin400,anonymous401; zero new failed-import runs", "PASS", smoke_path)
datasets_path = "artifacts/full-system-20261009/datasets-fixed/results.json"
for item in cases(datasets_path):
    if item["name"].startswith("TC"):
        row("Native decoded " + item["name"], "COMPLETED with all decoded input records", str({k: item[k] for k in ("record_count", "candidate_count", "pipeline_seconds") if k in item}) if item["status"] == "PASS" else "HTTP422; unsupported source/name contract", item["status"], datasets_path)
tc1_path = "artifacts/full-system-20261009/datasets-image-final/results.json"
assert passed(tc1_path) == 3
row("TC1 final image >10k path", "Worker/StageRunner serving commit succeeds", "10022records,83144candidates,73165serving pairs,2190proposals;98.734s including queue/shared load", "PASS", tc1_path)
stage_rows = []
for path in sorted((ART / "stages-final").glob("TC*/measurement.json")):
    m = json.loads(path.read_text(encoding="utf-8"))
    baseline = json.loads((ART / "stages-baseline" / path.parent.name / "measurement.json").read_text(encoding="utf-8"))
    stage_rows.append((path.parent.name, baseline, m))
    q = m["quality"]
    row("Offline execution " + path.parent.name, "Raw-only stages complete, truth post-evaluation", f"{m['records']} records;{m['candidate_count']} candidates;recall={q['candidate_recall']}", "PASS", path.relative_to(ROOT).as_posix(), "scripts/qa_benchmark.py --datasets ... --output ...")
row("Hard candidate recall", "No unexplained genuine-positive misses for readiness", "TC3 missing1608/4864;TC5 missing902/10582 in decoded CERTAIN subset", "FAIL", "artifacts/full-system-20261009/stages-final/results.json")
row("Synthetic global cross-batch/source", "A/B/C batches1/5/8 share retrieval opportunities", "Regression executed in full suite; global indexing/dedup asserted", "PASS", full)
row("Global sparse candidate probe", "Retain every baseline candidate; measure additional recall", "TC3 union140997 recall76.15%;TC5 union132628 recall95.87%;still misses,not default release", "PASS", "artifacts/full-system-20261009/global-candidate-probe/results.json", "scripts/qa_candidate_recall.py")
ops = "artifacts/full-system-20261009/operations/results.json"
row("Real worker/DB/storage recovery", "Claims/fencing/crash/outages recover without duplicate serving", f"{passed(ops)} PASS:12contenders,2workers,killed child,DB restart,storage pause,missing output", "PASS", ops, "scripts/qa_operations.py")
db = "artifacts/full-system-20261009/database-transactions/results.json"
row("Transaction/deadlock/Neon pool", "Rollback atomic,upsert idempotent,pool released", f"{passed(db)} PASS;40P01 detected;4concurrent Neon readers,9queries", "PASS", db, "scripts/qa_database_transactions.py")
cloud = "artifacts/full-system-20261009/real-cloud-final/results.json"
row("Real Neon+R2 integration", "Actual schema/API/pipeline/export checksum", f"{passed(cloud)} PASS;100records,in-process pipeline,41tables", "PASS", cloud, "scripts/qa_cloud_validation.py --output ...")
r2 = "artifacts/full-system-20261009/real-r2-extended/results.json"
row("Real R2 extended", "Multipart/stream/range/Parquet/concurrency/abort/cleanup", f"{passed(r2)} PASS;11.7MB multipart,1000Parquet rows,8objects/concurrency4", "PASS", r2, "scripts/qa_real_storage.py")
browser = "artifacts/full-system-20261009/browser-image-final/results.json"
row("Real Steward browser workflow", "Upload/review/golden/merge/export/history/split/audit", f"{passed(browser)} PASS;7source→5CSV rows→6after split;no console errors/failed requests", "PASS", browser, "node scripts/qa_browser.mjs --output ...")
browser_db = "artifacts/full-system-20261009/browser-db-image-final/results.json"
row("Golden/DB/export lineage + stale states", "Actions really persisted and exports immutable", f"{passed(browser_db)} PASS incl login;6golden values traced;source7unique;stale pair/preview409", "PASS", browser_db, "scripts/qa_browser_db.py --browser ... --output ...")
permissions = "artifacts/full-system-20261009/permissions/results.json"
row("Viewer actual API/ownership", "Read own,deny uploads/starts/exports/audit/other owner", f"{passed(permissions)} PASS;403mutations/404other owner", "PASS", permissions, "scripts/qa_permissions.py")
viewer = "artifacts/full-system-20261009/permissions/browser-results.json"
row("Viewer actual browser", "Upload hidden;export/golden disabled", f"{passed(viewer)} PASS;no console errors", "PASS", viewer, "node scripts/qa_viewer_browser.mjs")
double = "artifacts/full-system-20261009/double-submit/results.json"
row("Simultaneous execute", "2requests same token publish1export/event", f"{passed(double)} PASS incl login;equal CSV bytes,1export/merge/audit", "PASS", double, "scripts/qa_double_submit.py")
flags = "artifacts/full-system-20261009/flags-final/results.json"
row("V2/streaming/resume100,1k", "Pinned opt-in,private artifacts promoted,checkpoints/export replay", f"{passed(flags)} PASS;15/68checkpoints;100/1000records preserved", "PASS", flags, "scripts/qa_flags.py --output ...")
v2 = "artifacts/full-system-20261009/datasets-v2-engine/results.json"
row("V2 opt-in TC1 >10k", "Real worker generation completes with preserved records", f"{passed(v2)} PASS;10022records,83139candidates,17153pairs,23proposals,474.531s", "PASS", v2)
prod = "artifacts/full-system-20261009/production-mode/results.json"
row("Production lifecycle/cookie", "Read-only schema gate,secure session/logout", f"{passed(prod)} PASS;ASGI HTTPS TestClient + actual localPG/S3;not TLS deployment", "PASS", prod, "scripts/qa_production_mode.py")
row("Legacy Gemini helper canaries", "Semantic/explanation schema pass,no retry", "2 initial synthetic generation requests PASS", "PASS", "artifacts/full-system-20261009/gemini-canary.json")
row("Gemini explanation API first canary", "Valid schema and cache", "Request3 returned502 GEMINI_SCHEMA_ERROR;retained failed evidence", "FAIL", "artifacts/full-system-20261009/gemini-endpoint/results.json")
canary = read("artifacts/full-system-20261009/gemini-endpoint-final/results.json")
assert canary["status"] == "PASS" and canary["total_generation_requests_started"] == 4
row("Post-fix actual Gemini API/cache", "One bounded call then cache replay", "Request4 PASS;140input/106outputtokens;no second provider call;budget requests exhausted", "PASS", "artifacts/full-system-20261009/gemini-endpoint-final/results.json")
lint = read("artifacts/full-system-20261009/repo-lint.json")
row("CI-required backend Ruff", "ruff check backend src/tests/migrations exit0", f"FAIL with{len(lint)}findings;existing broad cleanup remains", "FAIL", "artifacts/full-system-20261009/repo-lint.json", "ruff check backend/src/ backend/tests/ backend/migrations/")
for name, reason in [
    ("21k/100k/1M benchmark", "Explicit user10k-only instruction"),
    ("Real deployed cloud worker/frontend/TLS", "Only in-process cloud pipeline/local independent workers proved"),
    ("Native full TC5 UI ingest", "Contract/formats unsupported;adapter does not prove native support"),
    ("OpenRouter real provider", "User selected Gemini;missing optional key,new router unwired"),
    ("Large-import/direct browser R2 path", "Router/UI not mounted/wired"),
    ("GroupD complete four-eyes active workflow", "Legacy case implementation differs from documented active route contract"),
    ("Live v2 mid-partition crash/resume", "Mock/unit failure points and successful opt-in proved;live crash not executed"),
    ("Cloud network/quota/cold-start recovery matrix", "Bounded real connectivity only;avoid blind load on staging"),
    ("All PG–R2 failure-boundary reconciliation", "Selected compensation/missing-output/crash cases covered;durable orphan daemon absent"),
    ("Native encoding/delimiter/long-field exhaustive matrix", "Negative CSV tests and QA raw decoder covered subset only"),
    ("Versioned old/new administrative registry accuracy", "Abbreviation/folding exists;complete domain accuracy unproved"),
    ("Sustained load/leak soak and frontend P95/P99", "Concurrent smoke is not a soak,large rendering or SLA test"),
    ("Full accessibility audit", "Basic roles/labels/focus/controls only"),
    ("Seven deep LLM audits", "Privacy/injection/hallucination/gray routing/reproducibility explicitly future work"),
    ("Remote CI", "Workflow read,local checks executed;no remote commit/push/run")]:
    row(name, "Independent execution evidence", reason, "NOT RUN", "")

(ART / "test-matrix.json").write_text(json.dumps(matrix, ensure_ascii=False, indent=2), encoding="utf-8")
text = "# Test matrix — 2026-10-09\n\nActual execution only. PASS is scoped to the described assertions. Archived FAIL rows remain; NOT RUN is never counted as PASS. Unit suites/targeted runs overlap, so do not sum them into a unique-test total. Artifact paths are local, ignored by Git; preserve them for handover.\n\n| Test | Expected | Actual | Status | Evidence |\n|---|---|---|---|---|\n"
for item in matrix:
    text += "| " + " | ".join(str(item[k]).replace("|", "/") for k in ("test", "expected", "actual", "status")) + " | " + (link(item["evidence"]) if item["evidence"] else "—") + " |\n"
text += "\n## Reproduction commands\n\nExact script entry points and environment boundaries are in [RUNBOOK](RUNBOOK.md). Machine `test-matrix.json` retains commands per case; fixture/output parameters must use a new directory. Do not rerun paid canaries:4/4authorized calls already consumed.\n\nThe original11backend failures, test-harness-only failures, browser locator/required-choice failures and first real API schema502 are retained in historical artifacts. Final actual image smoke/browser and post-fix Gemini evidence supersede them without rewriting outcomes. Source-record CSV identities are original external IDs; merge-event lineage uses internal UUIDs.\n"
(DOC / "TEST_MATRIX.md").write_text(text, encoding="utf-8")

perf = "# Performance benchmark — 2026-10-09\n\n## Environment and method\n\nWindows PowerShell;Python3.11.9;Node24.21.0;Docker29.8.1/Compose5.5.1,localPG16/Moto5.2.3. Host12logical CPUs/~15.34GiB RAM;Docker memory budget~7.43GiB. Runtime versions/image hashes in `final-runtime/results.json`;backend dependencies are not fully locked. Measurements are local observations,not production SLA.\n\nOnly five10k/seed-7 datasets. Raw mapping/decoder preserves IDs/source/provenance;CSV/CP1258/mixed/XLSX decoding here is QA adapter support. Ground truth read only AFTER computation;recall/auto-merge evaluation uses CERTAIN labels among decoded accepted records. TC5 has10010accepted+9rejected logical rows,30missing names;offline success does not imply native API support.\n\n`qa_benchmark.py` serial StageRunner measurements:wall/CPU and50ms RSS sampling of one host process,not whole-machine/container peak. Background Docker/test/build load can affect wall time;do not infer capacity at21k/100k/1M. Final stage runs use stable source-derived IDs. API imports create new UUIDs and may have TF-IDF tie-break count variation.\n\n## Baseline and final stages\n\n| Dataset | Records | Baseline wall s / CPU s / RSS GiB | Final wall s / CPU s / RSS GiB | Candidates | Proposals | Candidate recall | Auto false pairs (CERTAIN) |\n|---|---:|---|---|---:|---:|---|---:|\n"
perf_json = []
for name, b, m in stage_rows:
    q = m["quality"]
    recall = "N/A(no positive labels)" if q["candidate_recall"] is None else f"{100*q['candidate_recall']:.2f}% ({q['candidate_true_pairs']}/{q['known_positive_pairs']})"
    perf += f"| {name} | {m['records']} | {b['seconds']:.3f}/{b['cpu_seconds']:.3f}/{b['peak_rss_bytes']/1024**3:.3f} | {m['seconds']:.3f}/{m['cpu_seconds']:.3f}/{m['peak_rss_bytes']/1024**3:.3f} | {m['candidate_count']} | {m['proposals']} | {recall} | {q['auto_merge_false_pairs']} |\n"
    perf_json.append({"dataset": name, "baseline": b, "final": m})
perf += "\nFinal duration in milliseconds per stage:\n\n| Dataset | PREPARE | NORMALIZE | CANDIDATES | SCORE | DECIDE | CLUSTER | MATERIALIZE |\n|---|---:|---:|---:|---:|---:|---:|---:|\n"
for name, _, m in stage_rows:
    values = {s["stage_name"]: s["duration_ms"] for s in m["stages"]}
    perf += f"| {name} | " + " | ".join(str(values[key]) for key in ("PREPARE", "NORMALIZE", "BUILD_CANDIDATES", "SCORE", "DECIDE", "CLUSTER", "MATERIALIZE")) + " |\n"
perf += "\nThe sum of stages excludes parts of runner setup/validation/evaluation. Throughput must use the matching stage denominator,not total runtime. `benchmark-results.json` contains counts/timings/candidate metrics/CPU/RSS for calculations. Zero observed CERTAIN false auto pairs is not perfect overall matching;TC1auto pair recall44.75%,TC3zero,TC5~3.52%.\n\n## Verified optimization and changes\n\nOriginal review-lookup-only attempt103.328s vs97.812baseline:hashes equal,NO speed improvement. Later incident-pair guard index + one policy load:TC1total97.812→34.437s(~2.84x);CLUSTER60.171→1.687s(~35.7x),same83124candidates,2190proposals,exact decision+cluster hashes. RSS1.103→1.202GiB in that run,about100MiB index tradeoff. Evidence `optimization-comparison.json` and `guard-index-optimized/TC1_easy_match/measurement.json`.\n\nFinal calendar-validation runTC135.578s,cluster1.546s,RSS1.118GiB. Full final decision hash differs because scoring_model_hash pins changed source. Join-by-pair-ID comparison proves TC1–4business fields unchanged,only model fingerprint changed. TC5has4affected evidence/comparison/signature rows,3subgroup rows and1score from real calendar validation;candidate sets/recall and primary decisions preserved. See `final-decision-differences.json`;do not call the literal final decision hash equal to baseline.\n\n## Retrieval quality and complexity\n\nSynthetic A/B/C across batches1/5/8 and CRM/ERP/BILL is found through global index,with dedup/scoring assertions. Real label coverage still incomplete:TC3cross-batch3256/4864;TC5cross-batch7395/8212 andcross-source8781/9607. TC3misses1608,TC5misses902. Shared contacts and oversized buckets have caps;skipped_pair_observations counts overlapping blocking observations,not unique lost positives.\n\nGlobal sparse union probe retains every baseline candidate:TC3140997candidates,recall76.15%,generation9.938s;TC5132628candidates,recall95.87%,generation6.797s. Still1160/437misses. This probe is not default optimization. Global/partition sparse similarity can approach all-pairs work BEFORE Top-K;budget controls exist but Top-K alone is not complexity proof. Default index/candidate union are still in RAM;no1Mscalability claim. Comparison reduction ratio is~99.8%,but high reduction does not excuse recall loss.\n\n## Actual integration and infrastructure timing\n\nLatest final-image local worker smoke:100records4.109s,1k8.282s,4k23.609s pipeline timers;TC110022records98.734s,83144candidates,2190proposals. An earlier optimized-imageTC1run63.954s. These include queue/persistence/shared-load and random IDs,so cannot be used as controlled speedup.\n\nV2+streaming+resume100/1k completed with15/68checkpoints;timers7.157/24.375s. V210022completed474.531s,17153serving pairs,23proposals. Different constraints/semantics mean it is not a like-for-like accuracy/performance comparison. Checkpoint/partition ledger throughput dominated the observed long run;needs focused profiling before performance release.\n\nLocal100query samplesP50/P95/P99=2.764/3.601/5.373ms,poolcheckedout0,EXPLAINrun-id index/top-N. Neon9queries withconcurrency4:firstconnection5.766s,subsequentmin/median/max1031/1078/2484ms. Too few Neon samples forP95/P99/SLA;serverless cold-state unverified.\n\nRealR211.7MBmultipartupload9.453s(~1.24MB/s decimal payload);checksum/range+download case15.625s. Parquet1000rows1.453s;eightobjectconcurrency4case2.125s. Local10.5MBmultipart~0.234s in latest smoke. Network/storage locations differ,so no local-vs-cloud speed ratio claim.\n\nFrontend production images/real browser worked,with paginated lists and no console errors in covered flows. No crediblefrontendP95/P99/FCP/large-result render benchmark;NOT RUN. Docker idle resource snapshot is not peak/leak proof. Concurrent uploads/two workers/doubleexecute and killed-child recovery passed;no sustained soak or service-wide latency distribution. Upload request timings are available in local smoke `requests`,butparse-only latency/peak ingestion memory were not independently instrumented.\n\n## Scope limits\n\n21k/100k/1MNOT RUN per user instruction. The historical21k268261pairs/25.33s/1.03GiB/16357golden reference is not compared because dataset/policy/environment differ. Candidate quality and v2 throughput block readiness despite successful execution. CPU/RAM/disk/queue/service-wide measurements still need sustained workload instrumentation.\n"
(DOC / "PERFORMANCE_BENCHMARK.md").write_text(perf, encoding="utf-8")
(ART / "benchmark-results.json").write_text(json.dumps(perf_json, indent=2), encoding="utf-8")
gates = {"A": "PASS", "B": "BLOCKED", "C": "FAIL", "D": "PASS", "E": "FAIL", "F": "BLOCKED", "G": "FAIL", "H": "PASS_GEMINI_SCOPE"}
state = {"assessment_complete": True, "full_system_ready": False, "readiness": "NOT READY", "date": "2026-10-09",
         "gates": gates, "component_inventory": {"total": 25, "operable_with_scope_limits": 21, "gap_or_fail": 2, "unwired": 2},
         "llm": {"authorized_ceiling_usd": 0.1, "generation_requests_started": 4, "authorized_requests_max": 4, "retry_count": 0,
                 "more_paid_requests_authorized": False, "exact_billed_spend_reconciled": False},
         "credentials_current_scope_missing": [], "optional_unwired_provider": "OpenRouter", "experimental_runtime_flags_restored_off": True,
         "data_scale": "five10k only;21k/100k/1Mnotrun", "branch": "qa/full-system-20261009", "committed": False,
         "host": {"python": platform.python_version(), "logical_cpus": os.cpu_count(), "ram_bytes": psutil.virtual_memory().total},
         "reports": [p.relative_to(ROOT).as_posix() for p in sorted(DOC.glob("*.md")) if not p.name.startswith("CHECKPOINT")],
         "backend_full": {"passed": 884, "skipped": 1, "evidence": full}, "test_matrix": "artifacts/full-system-20261009/test-matrix.json",
         "remaining": ["candidate recall", "TC5 native ingestion", "v2 performance", "four-eyes product contract", "durable reconciliation/monitoring",
                       "full cloud worker/recovery/TLS validation", "LLM runtime/cost boundary audit", "CI-required lint210findings"]}
(ART / "audit-state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"reports": len(state["reports"]), "matrix_rows": len(matrix), "readiness": state["readiness"]}))
