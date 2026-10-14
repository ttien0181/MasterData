# Stage 2: reduce review effort without discarding evidence

The user requested execution of stage 2: remove low-value tasks, give short explanations, review clusters, and measure duplicate candidates outside the required queue. Implement inline on the current workspace, preserving existing work and the stage-1 contract. Do not create any Compose file.

## User flow

- Start a new pair workspace with the server's REQUIRED filter. Preserve explicit saved filters; all pairs and insufficient evidence remain available. Switching a group must retain the current review resolution unless restoring an explicit saved view.
- Display a short deterministic summary of matching identity anchors and conflicting fields directly in each row. Preserve complete evidence/details and optional AI explanation. Scores remain rule weights, never probabilities.
- Open a bounded cluster from a pair. Read the complete connected region of actionable/accepted/deferred links, with at most 20 members and 200 internal pairs. Hydrate each source record once; show source/name/contact/DOB/ID and pending/conflicting pair evidence. Never invent group membership from the current page alone.
- Batch acceptance uses the existing exact-pair-ID preview, reason checks, revision/token fencing, audit, queue and publication guards. It remains a human action. Do not call exception approval or infer that transitive links prove identity.
- Offer batch acceptance only for complete clusters with every member pair represented, no insufficient/unknown/blocked internal evidence or negative/rejected edge, and current manual eligibility. Explicit deferrals are visible. Incomplete or larger regions fall back to pair review; existing pair decisions are preserved.

## Evaluation

Keep truth out of matching and task selection. Count known same-entity candidate pairs outside required review, separately for insufficient and blocked evidence; report truth coverage and sampling. This is a candidate-level missed-work diagnostic, not end-to-end recall. Use synthetic real API/browser scenarios to compare actions and record exposures for individual versus cluster review, and verify identical final membership and retained decisions. Browser duration measures scripted execution, not human reading time.

## Limits

Do not change matching thresholds, soften evidence floor, enable auto-merge, or silently reassess old sessions. No fabricated precision certification. Do not delete low-value candidates. Stage 2 changes navigation and review granularity; calibrated matching remains a later stage.

## Research basis

Splink recommends both edge and cluster evaluation with labelled data: https://moj-analytical-services.github.io/splink/topic_guides/evaluation/edge_overview.html . Dedupe distinguishes informative active-learning labels from representative data needed for threshold evaluation: https://docs.dedupe.io/en/latest/how-it-works/Choosing-a-good-threshold.html . These support keeping audit/diagnostics separate from workload reduction.
