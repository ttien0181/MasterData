# Image scan triage — 2026-10-11

Frontend scan `S15-frontend-scan-02.sarif`: no vulnerable package detected. Removed unused global npm/corepack tooling from the standalone runtime and upgraded Alpine zlib. Actual packaged Next.js browser workflow still passes.

Backend scan `S15-backend-scan-02.sarif`: 29 reported findings; one HIGH remains, Debian zlib CVE-2026-85091. Removing runtime pip/setuptools/wheel eliminates the HIGH findings in their vendored copies without changing application dependencies. Migration, API and worker run successfully without package-install tooling. Runtime dependency lock remains unchanged.

Debian currently marks the installed trixie zlib version vulnerable and lists no fixed distribution package. Its notes discuss the affected upstream range; that uncertainty has not been turned into a blanket exception. Keep the finding OPEN pending a supported patched package or a documented, reviewed applicability assessment. [Debian security tracker](https://security-tracker.debian.org/tracker/CVE-2026-85091).

Do not hide findings through scanner exclusions, install an unverified binary, or claim a clean backend image. The other findings remain in SARIF for severity/applicability review. The CI browser image is a test tool, not a deployment artifact.

No production GO or accepted security exception is recorded.

## Resume — 2026-10-11

`S15/resume-live-frontend-scan-05.sarif`: actual live frontend scanned, 95 packages,
zero findings. `resume-frontend-scan-03` failed to resolve the config digest;
`resume-frontend-scan-04` targets the other local frontend image and is not live
candidate certification. These attempts remain separate.

`S15/resume-backend-scan-03.sarif`: 229 packages, 29 findings, one HIGH zlib
CVE-2026-85091. `resume-scan-rootfs.json` verifies scanned and current LLM backend
have identical RootFS layer digests, so package scan applicability is conserved.
Static triage: `resume-zlib-triage-03.json`, schema `triage-finding/v0`, verdict
`needs_review`, confidence high, review queue rank 1, boundary crossing unknown.

[Debian tracker](https://security-tracker.debian.org/tracker/CVE-2026-85091)
still marks trixie vulnerable/unfixed, while the description names 1.3.1.2–1.3.2.
[Upstream 1.3.1 writer](https://raw.githubusercontent.com/madler/zlib/v1.3.1/gzwrite.c)
and [upstream discussion](https://github.com/madler/zlib/issues/1310) provide
counterevidence for the installed 1.3.1 classification. No direct application
gzprintf/gzvprintf/gzwrite call was found; native-dependency reachability remains
unresolved. Absence of direct calls alone does not close the finding. Keep HIGH
open pending vendor resolution/authenticated installed-source applicability or a
supported patched package. No scanner exclusion, risk acceptance or source-system
writeback was performed. Remote required CI remains NOT_RUN.

Final candidate rescan after the history-download fix: `S15/resume-backend-scan-04.sarif`
reports the same 29 findings, including HIGH CVE-2026-85091 with no fixed version;
`S15/resume-live-frontend-scan-06.sarif` reports zero findings. Image/tag/layer
identities are recorded in `S15/resume-final-image-scans.json`. Earlier static
needs_review triage remains applicable to the unchanged installed zlib package;
the new application-layer fix is not a zlib remediation.
