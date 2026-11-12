# Mixed workload admission and durable outcomes

## Baseline evidence

The reference uses the shared Modal Sandbox in `us-east` with 1.5 physical CPU cores and 2 GiB, an independent 1-core and 1-GiB k6 Function, the authenticated TLS route, HikariCP maximum and minimum idle 16, and `commit_delay=0`. The 500 synthetic accounts, twenty 32-seat sections, 60-second ramp from one VU, 30-second full-level warm-up, 120-second measurement and 1.25-second minimum iteration period are retained. The controlled comparison uses one target section for the hot case and twenty deterministic targets for the distributed case. It keeps the same fixture and section capacities.

Successful mutation throughput counts unique intents acknowledged in the 120-second window and matched to a terminal PostgreSQL outcome. The intent latency starts at the first HTTP attempt and ends at the successful acknowledgement, so it includes timed retries. The post-test recovery requests and a durable commit without a timed acknowledgement are excluded. Historical k6 reports retain aggregate HTTP response percentiles. Those include failed HTTP responses and exclude attempts with no HTTP response. They are distinct from the mutation intent percentiles.

| Archived run | Enrollment/s | Enrollment p95 / p99 (ms) | Withdrawal/s | Withdrawal p95 / p99 (ms) |
| --- | ---: | ---: | ---: | ---: |
| [200 VUs](../infrastructure/.local/modal/runs/modal-20261108T071017Z-c47593/report.json) | 39.97 | 43 / 119 | 39.96 | 40 / 97 |
| [500 VUs A](../infrastructure/.local/modal/runs/modal-20261108T073014Z-af7e73/report.json) | 68.15 | 2,021 / 3,575 | 67.59 | 1,950 / 3,782 |
| [500 VUs B](../infrastructure/.local/modal/runs/modal-20261108T073708Z-d4ef30/report.json) | 47.79 | 3,205 / 5,788 | 47.04 | 3,146 / 5,742 |

| Archived route | A timeout / 503 / 502 | B timeout / 503 / 502 |
| --- | ---: | ---: |
| Enrollment POST | 46 / 14 / 0 | 209 / 6 / 0 |
| Withdrawal DELETE | 47 / 26 / 0 | 211 / 4 / 0 |
| GET current user | 39 / 35 / 0 | 210 / 7 / 0 |
| GET own enrollments | 55 / 29 / 0 | 216 / 6 / 0 |
| GET schedule | 42 / 16 / 0 | 178 / 8 / 0 |
| GET sections | 66 / 20 / 3 | 255 / 6 / 4 |
| Total in window | 295 / 140 / 3 | 1,279 / 37 / 4 |

The two failures included 50/273 ramp outcomes, 101/394 warm-up outcomes after ramp, and 6/33 additional measurement-drain outcomes. In measurement, 305/890 unexpected requests were reads. The remaining 133/430 mutations had the following durable evidence. Another attempt committed the same key for 113/311 requests. The timed-out attempt itself committed before the client's timeout for 5/23 requests and after timeout for 8/67. Cross-host clock proximity made the ordering uncertain for 7/29. A failed attempt associated with another attempt's commit does not establish whether the failed attempt obtained a connection or entered a transaction. There is no per-request PostgreSQL transaction phase for the read failures.

## Competition for JDBC connections

The archived measurement-window Hikari checkout means were 35.79 and 49.09 ms, with 65 sampled pending and a full pool in each run. Both checkout failures before transaction work and session-save failures at response commitment appear in Spring logs. Enrollment's durable claim-to-COMMIT p95 was 86 and 185 ms, while withdrawal's was 47 and 137 ms. The interval begins after checkout and omits transaction startup, so it is a lower bound on the connection's use. The archive cannot rank individual read-side connection checkouts by duration.

The first and second full 500-VU runs averaged 2.22/3.24 ms on student-term lock acquisition, 3.40/5.35 ms on section acquisition, 15.94/23.17 ms holding the section, and 2.48/4.25 ms in the observed COMMIT interval. Section blockers were repeatedly sampled during audit, idempotency completion and other writes. PostgreSQL also sampled WALWrite and WalSync. Session JDBC remained a major competing workload across each entire run. The primary normalized session read ran 116,252/89,058 times. The primary session UPDATE ran 76,954/59,106 times and emitted 409,189/312,036 WAL records. These cumulative statement figures include preparation, ramp, measurement and recovery. They do not partition individual JDBC waits or establish what fraction of commit latency was due to WAL.

## Local admission change

Enrollment already had a 64-command outer cap. It now admits at most half of its configured Hikari pool, rounded down with a minimum of one, to the proxied mutation transaction at once. At pool 16 that is eight concurrent transactions. The fair, bounded wait occurs before the transaction proxy can check out a JDBC connection. The wait is at most 3 seconds by default, additionally bounded by the remaining enrollment deadline. It releases the permit after completion or failure, before retry backoff. This leaves headroom for Spring Session JDBC, account validation and reads, which still use the same pool and retain their authentication and persistence semantics. Hikari connections are not reserved or pinned.

The limit is per backend process and is capacity control. PostgreSQL remains the coordination point across instances. The pool and mutation budgets for multiple processes add across replicas. The existing lock order, transactional constraints, capacity check, audit, durable idempotency and synchronous commit remain authoritative. No timeout is counted as success. A queue timeout uses the existing retryable overload response and is subject to the same zero-error and latency gates as Hikari timeouts. No 429 is introduced. Unit and integration coverage includes queue timeout before a claim, an available checkout while one mutation is blocked, successful completion of the next queued operation, cross-instance correctness, and the existing isolated 2,000-student capacity stress test.

The new evidence records the admission wait, the proxy-to-transaction-entry interval by operation, and time from transaction entry through completion. The proxy interval includes JDBC checkout and transaction setup. Hikari's own connection acquisition and usage are retained separately. The Student Portal browser test cuts delivery of both real committed responses, reloads the page, and checks replay of the original POST and DELETE keys. It requires a user confirmation before issuing DELETE and checks one enroll audit, one withdraw audit and a zero seat counter. This is independent of the synthetic runner's recovery.

## Cloud controls with admission enabled

The first provisioning attempt stalled while the local controller logged a DNS resolution failure. It was stopped before load and its App cleanup was verified. Three subsequent controls reached a 120-second measurement and passed the unchanged gate. The gate requires zero unexpected warm-up and timed outcomes, no interrupted iterations, p95 at most 1,000 ms, p99 at most 2,000 ms, the planned VU and iteration thresholds, SQL checks and successful cleanup.

| Control at 200 VUs | HTTP RPS | HTTP p95 / p99 (ms) | Enroll/s | Enroll p95 / p99 (ms) | Withdraw/s | Withdraw p95 / p99 (ms) | Hikari pending / PG lock waiters | Mean section hold (ms) | Durable outcomes |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| [A](../infrastructure/.local/modal/runs/modal-20261108T080823Z-98d5a6/report.json) | 239.83 | 86.31 / 166.02 | 39.97 | 112 / 217 | 39.97 | 96 / 178 | 0 / 0 | 3.09 | 14,388 |
| [B](../infrastructure/.local/modal/runs/modal-20261108T081319Z-bbc029/report.json) | 239.83 | 11.79 / 28.98 | 39.98 | 12 / 29 | 39.98 | 8 / 22 | 0 / 0 | 0.95 | 14,392 |
| [C](../infrastructure/.local/modal/runs/modal-20261108T081802Z-89e94b/report.json) | 239.70 | 76.07 / 141.68 | 39.95 | 66 / 161 | 39.95 | 52 / 114 | 0 / 1 | 1.31 | 14,386 |

All three controls reconciled every durable outcome, retained zero active episodes, and needed no recovery. Their throughput was within 0.13 RPS of the archived 239.80-RPS control. Tail latency varied between runs, including two p95s above the archived result.

## Two 500-VU attempts stopped on ramp

Both attempts used the same admission artifact and workload fingerprint. The unchanged k6 safety threshold interrupted ramp-up before the planned level and measurement window. Steady-state throughput and latency are unavailable. The following data describes the partial ramp only.

| 500-target attempt | Observed VUs / ramp seconds | k6 unexpected outcomes | Hikari peak pending / mean acquisition | Mean admission wait | Section wait / hold mean | Max sampled SQL lock wait |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| [A](../infrastructure/.local/modal/runs/modal-20261108T082244Z-a06dcc/report.json) | 438 / 53.58 | 666 | 40 / 4.17 ms | 189.51 ms | 2.20 / 11.49 ms | 17.36 ms |
| [B](../infrastructure/.local/modal/runs/modal-20261108T082509Z-691985/report.json) | 269 / 33.25 | 219 | 18 / 10.70 ms | 482.73 ms | 5.41 / 26.67 ms | 10.83 ms |

The 666 and 219 timed failures were HTTP 503 `SERVICE_OVERLOADED`. Enrollment accounted for 403 and 115 and withdrawal for 263 and 104. All 666 in A and 208 of 219 in B returned in under one second. The three-second JDBC admission wait could not have expired for those responses. The 64-command outer cap filled while requests waited in the new admission queue. The resulting rejection moved overload ahead of JDBC. The lower sampled Hikari pending counts describe shorter, lower-VU windows than the archived full 500-VU tests. They cannot establish improved 500-VU service.

| Partial ramp | Acknowledged enroll/s | Enroll p95 / p99 (ms) | Acknowledged withdraw/s | Withdraw p95 / p99 (ms) |
| --- | ---: | ---: | ---: | ---: |
| A | 33.28 | 1,576 / 1,912 | 30.85 | 1,348 / 1,850 |
| B | 19.10 | 2,385 / 2,753 | 16.75 | 2,339 / 2,729 |

The transaction-entry-to-completion means in the sampled ramps were 35.57/90.93 ms for enrollment and 25.57/61.47 ms for withdrawal. Proxy entry including checkout and transaction setup averaged 11.66/40.39 ms for enrollment and 14.50/34.98 ms for withdrawal. The shorter withdrawal transaction and generally longer enrollment body are consistent with the archived claim-to-COMMIT evidence. These intervals and the Hikari timer have different sample populations.

All 3,632/1,379 durable records had a logged intent and the eight structural SQL checks returned zero violations. Full reconciliation failed. Aborts interrupted 437/271 iterations and left 109/114 mutation starts without a completed client response. The bounded worker recovered 64 of 309 and 64 of 169 pending journeys and recorded 0/19 stored replays. PostgreSQL still contained 124/65 active episodes before Sandbox teardown. The remaining episodes were recorded in the evidence. Both isolated databases were removed as part of verified resource cleanup.

Admission stays available through `ENROLLMENT_JDBC_ADMISSION_ENABLED=true` and is disabled by default for the final demo. The exact experimental JAR and results remain available for reproduction. Both 500-target attempts failed qualification, so the 1,000-VU tier stays gated.

## Single section and distributed sections at 200 VUs

The two successful windows used the same admission artifact, resource limits and seed. The durable enrollment targets were exactly one and twenty sections, respectively. The distributed comparison is also the third independent control above. An additional distributed repeat lost controller connectivity after readiness. Its metrics are unavailable and its App cleanup was verified. [Single-section run](../infrastructure/.local/modal/runs/modal-20261108T082724Z-e8e715/report.json) · [Interrupted extra distributed repeat](../infrastructure/.local/modal/runs/modal-20261108T083213Z-5142e7/report.json).

| Targeting | HTTP p95 / p99 (ms) | Enroll/s, p95 / p99 (ms) | Withdraw/s, p95 / p99 (ms) | Section acquisition / hold mean (ms) | Peak PG lock waiters / longest sampled wait |
| --- | ---: | ---: | ---: | ---: | ---: |
| 20 sections | 76.07 / 141.68 | 39.95, 66 / 161 | 39.95, 52 / 114 | 0.16 / 1.31 | 1 / 45.59 ms |
| One section | 37.80 / 83.22 | 39.97, 40 / 90 | 39.97, 33 / 76 | 0.32 / 1.49 | 2 / 14.31 ms |

Both passed with zero HTTP errors, zero Hikari waiters, zero structural SQL violations and zero active episodes after the workload. The single-section test reconciled 14,390 durable outcomes and needed no recovery. It had higher mean section acquisition time and peak sampled waiter count, with similar successful throughput and a lower HTTP tail in this pair. The single repeat and shared-host scheduling limit a causal latency comparison. This profile spreads the start and mutates only on every fourth iteration. It is distinct from the one-shot seat-allocation stress test.

## Matched reference and successful reads

The previous backend was rerun at 200 VUs using the new workload and telemetry. The artifact records differ only in backend JAR SHA-256. The refreshed reference passed with 239.48 HTTP RPS, p95/p99 196.80/391.28 ms, 39.90 acknowledged enrollments/s at p95/p99 242/421 ms, and 39.89 acknowledged withdrawals/s at 210/392 ms. It had zero HTTP errors and reconciled all 14,378 durable outcomes without recovery, with zero active episodes. Hikari pending peaked at 40. Section hold averaged 9.59 ms and the longest sampled PostgreSQL lock wait was 38.73 ms. [Refreshed reference](../infrastructure/.local/modal/runs/modal-20261108T095227Z-1be2f7/report.json).

| Successful read | Reference req/s | Reference p95 / p99 (ms) | Admission median req/s | Admission median p95 / p99 (ms) |
| --- | ---: | ---: | ---: | ---: |
| Sections | 39.92 | 195.92 / 384.21 | 39.98 | 89.06 / 157.66 |
| Schedule | 39.93 | 178.65 / 378.12 | 39.97 | 81.54 / 144.31 |
| Own enrollments | 39.94 | 162.95 / 363.25 | 39.97 | 75.57 / 135.61 |
| Current user | 39.89 | 144.17 / 361.90 | 39.97 | 66.62 / 121.39 |

The three admission controls preserved successful read throughput and had lower read percentiles than this refreshed reference. The earlier reference on the same backend measured overall HTTP p95 43.47 ms, below two of the admission controls. The observed variation prevents a general read-latency improvement claim. The archived run lacks per-route success percentiles, and the 500-target admission runs lack a steady-state window.

## Final demonstration and cleanup

Use the [final demo guide](final-demo-guide.md) and leave the experimental admission flag at its default. The three admission controls and the hot test passed at 200 VUs. The two prior full 500-VU attempts and both admission attempts failed. The 1,000–5,000 VU tiers remain Not Tested. In Performance Lab, use the run IDs above to distinguish timed business acknowledgement, durable commit evidence and synthetic recovery. The current default configuration also passed the real-browser replay test for both lost responses.

Modal attributed **USD 0.30377613 before credits** to this investigation, including the interrupted distributed repeat. The two 500-target attempts accounted for USD 0.03987885. The initial provisioning interruption had no attributed billing rows. The workspace billing summary showed USD 0 billed. All temporary Apps, Sandboxes and generator containers were stopped.

Verification covered 96 JVM tests and the isolated capacity stress task, 16 Python benchmark tests, the Go gateway, seven frontend unit tests, production build and six Performance Lab browser tests. Four Student Portal browser tests passed. The live monitoring browser test was skipped in the isolated demo without a Prometheus stack. The lost-response browser test passed again with admission disabled. The archived benchmark files and Flyway V1–V7 matched their saved hashes.
