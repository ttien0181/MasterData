# Pair classification: stage-1 correctness verification (2026-10-13)

## Result

Assessment v3 preserves insufficient evidence as group C. Such undecided pairs remain inspectable and temporarily separate, without blocking publication. An explicit human deferral still requires review. SQL summaries/filters and Python publication use the same mandatory-review contract. Weak edges no longer spread pending regions; accepted links and constraints remain effective.

Migration 0030 was already present as an untracked workspace file. Its upgrade and nullable varchar(500) review_reason column were verified on SQLite tests and local PostgreSQL. Include that file when integrating this workspace.

## Data and matched before/after comparison

Source: https://drive.google.com/drive/folders/1ZSRF1p6OY9hCglV95jebNcOZusQ_D1Ws . Raw inputs and truth are cached outside the repo. Manifest checks cover 423 required input/truth files across 8 bundles: no missing files or SHA256 mismatches. TC1-5 use the 10k seed-7 bundles; TC6/8/13 use deterministic 12k samples from 100k seed-7 bundles. Samples change frequency context and do not establish full-100k performance.

The baseline is the actual pre-edit dirty workspace at db7ec34, preserved externally, not a clean commit reconstruction. Both runs use identical loaders and the same unchanged address reference dataset (SHA256 70a0409148c9d75847f9f5e539b862505a830544607dab578c0caa80aa0ba7dd). All 8 candidate hashes and score/signature hashes agree. Truth is opened only after matching. No floor failure is promoted to REVIEW after the fix.

| Case | Loaded / available | Candidates | Mandatory before | Mandatory after | Same-entity floor failures* |
|---|---:|---:|---:|---:|---:|
| TC1 | 10,022 / 10,022 | 83,124 | 70,143 | 4,166 | 0 |
| TC2 | 10,000 / 10,000 | 72,213 | 59,003 | 1 | 0 |
| TC3 | 9,992 / 9,992 | 90,129 | 77,288 | 1 | 2,527 |
| TC4 | 10,014 / 10,014 | 79,522 | 61,765 | 2,460 | 0 |
| TC5 | 10,010 / 10,010 | 90,684 | 71,470 | 4,608 | 4,204 |
| TC6 | 12,000 / 100,000 | 117,017 | 117,017 | 1,168 | 93 |
| TC8 | 12,000 / 100,000 | 107,544 | 107,544 | 339 | 3 |
| TC13 | 12,000 / 100,000 | 127,511 | 127,511 | 972 | 13 |

*These are known same-entity candidate pairs that fail the existing evidence floor. They remain stored, but are outside mandatory work. Queue reduction does not establish preserved duplicate recall. This is a material limitation for calibrated matching and soft-anchor follow-up; do not treat it as evidence that automatic merging is ready.

TC5 contains mixed CSV/XLSX exports, header offsets, date serials and malformed/footer/repeated-header rows. The loader rejects 65 structural rows and loads 10,010 records versus manifest 10,019; 9 manifest records are not importable under these structural rules. Gold mapping covers 9,801 loaded records. TC5 measurements apply to this parsed subset. No truth labels are used to repair or select raw rows.

## Verification

- Backend full isolated suite: 1,105 passed, 2 skipped (302.99s). The final repeated-header regression also passed after the loader fix (3 benchmark-loader tests).
- Frontend: 200 tests / 27 files passed; TypeScript check and production Docker build passed.
- Existing Compose only: local PostgreSQL/API/frontend healthy; worker running, schema 0030 and assessment v3. Local configuration explicitly excludes external DB/R2/LLM calls.
- Real browser/API/worker: mixed case total 3, insufficient 2, mandatory 1; explicit defer raises mandatory to 2; resolution and guarded publication produce 2 groups. All-insufficient case publishes 3 singletons while all 3 pairs remain UNDECIDED. Final run has no page errors.
- Independent final review found no Critical/Important production defect. Its important loader finding (XLSX/header offset/date serial) and misleading filter text were corrected. Reviewer additionally probed 360 SQL/Python routing combinations and hard-constraint acceptance cases.

Browser evidence: frontend/artifacts/pair-correctness-stage1/result.json and screenshots. A failed smoke attempt selected a different pair after sorting; the existing cluster guard correctly blocked the weak accepted edge. The driver was corrected to track the deferred pair ID, and the final run passed.

## Integration and next stage

Changes remain uncommitted to preserve the user's existing edits. Old assessed sessions retain history; explicitly reassess them to apply v3. Auto-merge remains disabled for reviewed assessments. Stage 2 addresses navigation, short evidence and group review; representative labelled precision/recall and cluster evaluation must precede any auto-merge activation.

Splink explicitly recommends evaluating both edges and clusters against labels: https://moj-analytical-services.github.io/splink/topic_guides/evaluation/edge_overview.html . Dedupe cautions that active-learning labels are not representative threshold-evaluation samples: https://docs.dedupe.io/en/latest/how-it-works/Choosing-a-good-threshold.html . These support reporting workload savings separately from quality certification.
