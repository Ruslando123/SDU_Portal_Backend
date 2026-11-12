# Phase 8B Modal smoke verification

The first timed Modal smoke passed on November 7, 2026. Run `modal-20261107T114747Z-2d644d` used one Sandbox for PostgreSQL 18.6 and Spring Boot 4.1.2 and one separate Modal Function for k6 2.3.0. Both resources reported `us-east` at runtime. The Sandbox used ephemeral local storage. [Deployment runbook](modal-deployment.md).

## HTTP and SQL evidence

| Observation | Result |
| --- | ---: |
| Synthetic logins with CSRF and secure cookies | 10 |
| Session preparation | 1.852 s |
| Timed virtual users | 10 |
| Completed journeys | 10 |
| Timed requests | 50 |
| Timed duration | 1.013 s |
| Achieved HTTP request rate | 49.38 req/s |
| HTTP p95 | 406.81 ms |
| HTTP p99 | 493.54 ms |
| Unexpected outcomes | 0 of 50 |
| k6 exit | 0 |

Each journey validated course listing, enrollment, own schedule, withdrawal and a byte-identical enrollment replay with the original Idempotency-Key. Login and both CSRF requests ran before the timed window. HTTP duration includes the authenticated Sandbox Connect path. The short one-iteration burst measures cloud functionality and yields descriptive percentiles. Production SLO and sustained cloud capacity are **Not Tested**.

The independent PostgreSQL check returned zero violations for all eight invariant queries. It found 10 unique students, 10 withdrawn episodes, 0 active rows, section counter 0, 10 ENROLLED outcomes, 10 WITHDRAWN outcomes and 20 audit events. It verified Flyway V1–V7, PostgreSQL listening on loopback, and `fsync`, `synchronous_commit` and `full_page_writes` all on.

The generator recorded 401 without the Connect Token, 403 without the gateway key, 401 without a student session, and 404 for both management readiness and Prometheus on the gateway. Internal readiness independently reported UP before the generator was invoked. Raw points carry bounded request names and no URL, credentials or cookie labels.

## Resources, cleanup and billing

k6 used 0.11 CPU-seconds and reached 132.25 MiB peak RSS. Sandbox limits were 1.5 physical cores and 2 GiB. Generator limits were 0.5 physical cores and 512 MiB. The Sandbox did not expose the cgroup usage fields queried by the collector, so measured Sandbox CPU and peak RAM display **No Data**. Modal verified the App stopped with zero active Sandboxes and containers. The recorded Sandbox exit 137 followed the operator's explicit termination after evidence collection.

| Item | USD before credits |
| --- | ---: |
| Estimated compute at the full limits | 0.03204542 |
| Estimate including the 1-GiB traffic allowance | 0.07204542 |
| Successful smoke App, reported metered usage | 0.00366814 |
| Three preparation attempts, reported metered usage | 0.02595380 |
| Phase 8B App total observed in the billing report | 0.02962194 |

Metered amounts come from the Modal billing report matched by exact App ID and `sdu_run` tag. They describe provider-reported usage before credits. Net invoice charges and workspace-wide egress adjustments are separate. [Modal billing](https://modal.com/docs/guide/billing).

## Preparation failures and repeatability

| Stage | Finding | Resolution |
| --- | --- | --- |
| First App setup | Serialized function used local Python 3.12 with a Python 3.13 image. | Pinned the generator image and SDK controller to Python 3.12. |
| Second App setup | This workspace rejected the explicit AWS provider pin. | Retained the narrow region on both resources and let Modal select the provider. |
| Third readiness attempt | The controller lost DNS connectivity to Modal before it could verify readiness. | Verified the expired resources after reconnection. Used a macOS idle-sleep assertion for the successful run and fixed the extracted runtime permissions before retrying. |

None of those attempts started a timed k6 workload. All four Apps were verified stopped. The final cloud run is stored separately from the Phase 6 archive. The Performance Lab Cloud runs section exposes a server-projected report with HTTP and SQL results, limits, available usage, and reported cost. The evidence directory retains sanitized backend, PostgreSQL and k6 logs, the compressed raw points, the k6 summary and the independent SQL report. [Run evidence](../infrastructure/.local/modal/runs/modal-20261107T114747Z-2d644d/report.json), [k6 summary](../infrastructure/.local/modal/runs/modal-20261107T114747Z-2d644d/k6-summary.json), [SQL invariants](../infrastructure/.local/modal/runs/modal-20261107T114747Z-2d644d/invariants.json).

The cloud fixture, hard limits and workload support one 10-VU smoke. The next 50–200-VU experiment needs a separately sized fixture, a controlled login strategy, a longer measured window, observed Sandbox resource telemetry and a new reviewed budget. The current runner remains fixed at 10 VUs.
