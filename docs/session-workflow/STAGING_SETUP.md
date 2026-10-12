# HTTPS staging — BLOCKED pending target

User confirmed no HTTPS staging target is available yet. Local HTTP QA and
production images do not satisfy R12. Supply a QA host/domain, certificate method,
database/R2 scope, secret delivery channel, alert sink and recovery/on-call owners
before staging validation. Never copy `.env.qa` into an image or source repository.

Deploy exactly recorded image digests and migration head from the final candidate
manifest. Run additive migration once before API/worker; verify database/schema
identity. Preserve old binary and backups before rollout. When migrations are not
destructively reversible, rollback binaries against compatible schema or forward
fix; do not drop workflow/history columns or restore over production blindly.

TLS proxy must route frontend and API/WS, preserve forwarding headers and pass
the exact HTTPS origin to CORS/Origin/CSRF configuration. Verify actual Secure/
SameSite cookies, unauthenticated/forbidden/owner responses, WS reconnect and R2
presigned browser PUT/GET with staging-origin CORS. R2 endpoint is HTTPS account
endpoint without a bucket path; keep bucket private.

Exercise golden workflow, eligible provider canary within remaining shared budget,
reload/new browser, historical download SHA, worker readiness, restore and alerts
on staging. Record domain, digests, config names, migration head, IDs and assertions,
never secrets/signed URLs. Only then assess R12. Production cutover remains a
separate user instruction after all required gates pass.
