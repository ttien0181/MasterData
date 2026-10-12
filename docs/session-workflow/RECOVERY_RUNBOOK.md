# QA recovery and production prerequisites

Verified drills are isolated to project `masterone-workflow-live`, database
`masterone_qa`, and bucket `masterone-qa`. Never reuse these commands against
production. Retain every backup/object manifest and old export for the retention
period; do not run `down -v`, prune or a shared-bucket sweep.

`scripts/qa_workflow_recovery.py --source <verified-live-browser.json> --output
<fresh-campaign-directory>` checks ownership, rejects active tasks, creates a
custom PostgreSQL dump and new `workflow_restore_*` database, compares table hashes,
copies only the selected run's uploads/workflow/exports objects to a new QA prefix,
reads back checksums, then restarts the owned API/worker and verifies business hashes.

`scripts/qa_workflow_restore_app.py --source <recovery.json> --output <fresh-dir>`
temporarily stops the QA API, starts the same packaged image on the restored DB
and `R2_KEY_PREFIX=qa-restore/<clone>/`, and checks login, profile, pairs, clusters,
golden, audit/history and immutable download through app/browser. Only the restored
QA owner's test password changes; credentials remain in memory. Finally it stops
the restore container and starts the original QA API. The stopped restore container,
clone DB, backup files and objects remain for inspection.

Evidence `S13/recovery-03`: DB restore 1.469 seconds for this tiny synthetic fixture
(not a production RTO commitment), 10 objects copied/read back; original business
hashes survive restart. `S13/restore-app-01`: packaged browser and download PASS
against clone + copied prefix. Prefix adapter rejects traversal for reads/writes/
presign/list/delete; an empty prefix preserves existing historical key naming.

Additional completed drills: `S13/faults-05` kills owned worker in PREPARE/DECIDE/CLUSTER and verifies expired-attempt fencing/unique task/all 200 memberships. `S13/restore-app-02` deletes only a shadow export object, verifies HTTP 409 and restores bytes. `S13/database-outage-01` stops only the owned idle DB, checks API readiness 503 and worker heartbeat failure, restarts DB/worker and reconciles unchanged export hash. `S13/observability-01` delivers worker-down alert to a temporary local QA sink and gathers fifteen idle samples.

`S13/soak-01` completed three sequential three-record synthetic packaged workflows in 127.531 seconds with four resource/PG connection samples, proxy intake and LLM disabled. This bounded repetition passed; it does not certify concurrency or sustained production capacity.

Still required: approved production load-profile verification, configured production alert delivery, approved production RPO/RTO, retention/on-call ownership and recovery environment. Idle samples and tiny-fixture restore do not certify load or production recovery. No production readiness claim.
