# Provider verification — 2026-10-11

## Paid-account follow-up (supersedes the initial blocker below)

User confirmed the project has billing enabled: “Project đã bật billing (paid)”.
The standard [Gemini API terms](https://ai.google.dev/gemini-api/terms), effective
2026-03-23, classify API access from an active billing project as paid services.
Paid prompts/responses are not used to improve products; limited safety/abuse
logging still applies. This is not zero retention or an organization-specific
contract. Approval here is limited to synthetic QA data, not production PII.

Evidence: `artifacts/session-workflow/20261011-workflow-01/S10/paid-account-evidence-01/provider.json`.
Verified profile: Gemini only, `gemini-3.5-flash-lite`, output cap 1024, retry 0,
timeout 30 seconds, semantic maximum 10 pairs, 3 microUSD/token upper reservation.
The model requires supported `thinkingLevel: MINIMAL` when the profile requests
the former budget-0 behavior; this does not mean reasoning is disabled. Output
usage includes thinking tokens when the provider supplies them. See
[thinking documentation](https://ai.google.dev/gemini-api/docs/thinking).

`S10/paid-live-04/live.json` and `browser/browser.json`: real ADDRESS_COMPARE and
EXPLANATION SUCCESS through packaged worker/API and mounted browser UI. Reload
uses the same request ID and no additional dispatch. The pinned campaign ledger
now contains 4 outbound attempts, 13,250 reserved tokens and 39,750 microUSD,
including failed/superseded attempts. Caps remain 20 / 100,000 / 1,000,000; no reset.
Reservation is not invoice cost; unavailable failed-request usage stays unknown.
The DB emergency switch was disabled after verification. `.env.qa` is unchanged;
credentials were supplied only through process environment and never in evidence.

## Initial assessment (historical)

Official model page verifies the configured `gemini-3.5-flash-lite` stable model
supports structured output. This does not prove this QA account has access:
[Google model documentation](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash-lite).

[Official standard pricing](https://ai.google.dev/gemini-api/docs/pricing.md)
lists USD 0.30 per million input tokens and USD 2.50 per million output tokens
including thinking for this model. The current integer microUSD-per-token policy
can use 3 as a conservative upper bound for text input/output, without grounding,
audio or other charged tools. This is an accounting bound, not invoice measurement.
No provider flags/prices were approved or changed by this documentation alone.

Public pricing distinguishes free/paid data use. It does not establish account
billing tier, retention or the user's organization contract. QA synthetic-only
retention scope and account evidence remain to be supplied/verified. Never copy
this public page into `*_RETENTION_VERIFIED=true` as if it proves account approval.

OpenRouter is unsupported for this profile: no QA credential identified. Shared
smoke budget remains 20 outbound attempts / 100,000 reserved tokens / 1,000,000
microUSD across S10/S12/S15/S16, including retry/fallback. Live requests have not
yet been dispatched. Existing DB accounting must be pinned to one campaign QA
database before live tests; recreating a database must not reset this allowance.

Final candidate refresh: S10/paid-live-06 passes all five real worker/UI/cache
checks on the same four images as final direct06/candidate04. Shared campaign
accounting-final.json retains all failed and successful requests:8attempts,
26874reservedtokens,80622microUSD reservation (not invoice), global switchfalse.
Runtime capability flags stay true; persisted policy disables dispatch. Provider
profile applies to synthetic QA only. The current explanation screenshot correctly
describes missing email; historical factual errors remain, and one response does
not establish general accuracy. No further paid calls are needed for doc/harness-only edits.
