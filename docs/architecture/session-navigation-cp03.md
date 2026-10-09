# CP03: Session navigation and Hub

The workspace keeps the selected run and view in the URL (`?run=<run-id>&view=<overview|import|auto|review|history>`). The URL wins over the per-user `masterone.resolution-run.v1.*` local-storage hint. When no run is in the URL, startup restores the saved run if it is in the account-scoped run list, then a queued/running run, then the newest run. If no run exists, the workspace stays empty.

The existing `GET /api/v1/mdm/runs?limit=&offset=` endpoint supplies account-owned rows and server pagination; CP03 adds no backend routes or persistence. The Sessions Hub loads pages of 25, shows a metadata preview, and changes the workspace only after “Mở phiên”. Closing the Hub leaves the current workspace and scroll position intact. A run switch clears run-scoped dialogs and remounts the resolution flow; choice drafts keep their existing per-user/per-run keys.

## Manual verification

- Open a direct link with `?run=<owned-run>&view=review`, refresh it, and confirm the top bar, overview, review queue, and export all refer to that same run.
- Navigate between overview, review, and history; use browser Back and Forward and confirm the view follows the URL. Confirm an unauthorized `view=import` falls back to overview for a read-only role.
- Open Sessions, select a row to preview it, close the Hub, and confirm the workspace and scroll position did not change. Reopen, choose “Mở phiên”, and confirm the URL and top bar update together.
- With more than 25 runs, page forward and backward and confirm rows and page counts come from the server. An empty account should show the empty workspace and an empty Hub.
- Switch quickly from run A to B while A's overview request is delayed; only B may update the dashboard. Repeat while a queue view is open and confirm run-scoped selections/dialogs clear.
- Open a missing run ID and an ID owned by a different account. Both should fail safely without exposing the other account's metadata, then restore an account-owned recent run.
- Block local storage and repeat direct-link, refresh, and fallback startup. The URL and server list should continue to work.
- Make the run-list request fail, retry from the Hub, then verify the list recovers without replacing the selected run.
