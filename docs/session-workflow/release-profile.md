# Workflow release profile â€” campaign 20261011-workflow-01

Date: 2026-10-11, Asia/Bangkok. Candidate is dirty source plus manifest hashes, not HEAD alone.

User authorized the complete S00â€“S16 checklist in this conversation and direct execution without the unavailable superpowers skill. Execute sequentially without subagents. Preserve inherited index, worktree and paused benchmark evidence. No production cutover or push.

- Group D: approved by the user on 2026-10-11 in this conversation: one owning Steward may manually merge D when every server hard constraint passes. No auto merge D; ownership/capability/revisions/audit remain required. This release does not advertise two-person approval or sharing.
- Formats: user confirmed CSV-only release on 2026-10-11. No native Excel support is advertised. Preserve extra-field provenance and explicit rejected-input/quarantine coverage; do not claim full TC5 acceptance by projecting XLSX to CSV.
- Scale/quality/SLO: user has not approved thresholds. Optimize from measurements and general characterization tests; R10 cannot PASS without approved targets.
- Benchmark override: each benchmark attempt randomly selects one TC, then its smallest available record-size dataset. Do not run the entire 100k suite from S14; no capacity certification beyond actual evidence. Keep truth out of matcher/provider input.
- QA credential source: user identifies `.env.qa`. Inspect presence only in reports; use secrets privately through env/config. No modification to that file.
- Local baseline: project `masterone-workflow`; PostgreSQL 16; Moto storage (not R2 evidence); isolated ports 15447/19017/18017/13017; images `masterone-workflow-backend` and `masterone-workflow-frontend`.
- Engine flags: baseline OFF; do not change matcher thresholds/defaults to pass cases. Versions pinned per run.
- Live scope: synthetic data only, QA-owned database/schema and bucket or enforced prefix. Live resource identities and HTTPS staging target still require verification from configuration.
- LLM: gateway/policy only; Gemini primary. OpenRouter has no key in `.env.qa` presence inventory, so backup unsupported until configured and verified. Retention/model/price must be evidenced. Shared smoke cap 20 outbound attempts, 100,000 tokens, 1,000,000 microUSD reservations across S10/S12/S15/S16. Benchmark cap not approved.
- Deployment: user confirmed HTTPS staging is not available yet and requested a note for later setup. R12 staging remains BLOCKED; continue local QA/packaging. Alert sink, recovery owner and production limits are not yet verified. Credentials alone do not certify these gates.

Every gate remains NOT_RUN, FAIL or BLOCKED until candidate-specific evidence proves PASS.

S10 follow-up (2026-10-11): user confirmed Gemini project billing enabled; standard
paid terms approved only for synthetic QA, with limited safety logging. Packaged
semantic and UI explanation succeeded on `gemini-3.5-flash-lite`, output cap 1024,
MINIMAL thinking, no retries, 30-second timeout, maximum 10 semantic pairs/run.
Price reservation remains 3 microUSD/token. Campaign ledger now reserves 4 calls /
13,250 tokens / 39,750 microUSD; caps and database pin are unchanged. DB emergency
switch is disabled after testing. See S10 handoff and provider evidence; production
data-use approval, S11 direct CORS and HTTPS staging remain separate open gates.
