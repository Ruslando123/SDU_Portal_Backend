# Modal private Sidecar mixed test at 1,000 VUs

`modal-20261108T131646Z-ead379` failed. k6 observed 1,000 VUs during ramp and measurement. The measurement stopped after 5.155 seconds when the k6 safety threshold tripped. The run produced no qualified 120-second throughput result. The passed comparison is `modal-20261108T125901Z-c6de7a`.

## Configuration and preflight

Both runs used `us-east`, one Spring Boot backend and one PostgreSQL 18.6 Sidecar on the private bridge, a separate authenticated TLS edge with HAProxy, and one separate k6 container. The backend and database shared a verified 8 CPU / 16 GiB cgroup. Edge and generator limits remained 1 CPU / 1 GiB and 2 CPU / 2 GiB. The JVM maximum heap was 4 GiB, with four available processors. Both used the same backend JAR, k6 script, HikariCP pool and minimum idle of 16, and PostgreSQL configuration. The database retained `shared_buffers=1GiB`, `effective_cache_size=4GiB`, `max_connections=60`, `commit_delay=0`, `fsync=on`, `synchronous_commit=on` and `full_page_writes=on`.

The preflight found 5,000 synthetic accounts, 20 distributed sections with 252 seats each, and zero active enrollments. Session preparation authenticated 1,000 accounts. The benchmark assigned at most 50 actors per section. PostgreSQL used SCRAM and JDBC `sslmode=verify-full` and had no exposed port. Edge tests observed HTTP 403 without the gateway key, 401 without a student session and 404 for readiness, administration and metrics paths. The planned sequence was a 60-second ramp from 1 VU, 30 seconds of warm-up at the target and a 120-second measurement.

The 1,000-VU harness raised the edge caps from 142,500 to 285,000 requests and from 512 to 1,024 MiB of cumulative response bodies. The per-response cap stayed at 64 KiB. It also increased the bounded evidence buffers and added 100 seconds to the hard timeouts for preparation and evidence collection. The resulting Sandbox and generator timeouts were 728 and 538 seconds. Eight live SQL safety snapshots completed without violations or missed reads. The workload script, database state preparation, resource allocations and connection budget matched the 500-VU run.

## HTTP and business outcomes

| Metric | Passed Sidecar 500 | Sidecar 1,000 |
|---|---:|---:|
| Observed VUs | 500 | 1,000 |
| Measurement runtime | 120 s complete | 5.155 s before abort |
| Requests | 71,915 | 2,150 |
| Qualified HTTP RPS | 599.29 | — |
| HTTP p95 / p99 | 46.60 / 75.60 ms | 2,296.60 / 2,400.77 ms |
| HTTP unexpected outcomes in k6 measurement | 0 | 131 HTTP 503 (6.09%) |
| Interrupted iterations | 0 | 1,000 |
| Acknowledged enrollments / withdrawals | 11,984 / 11,982 | 220 / 133 |
| Acknowledged enrollments / withdrawals per second | 99.87 / 99.85 | 42.68 / 25.80 in the partial window |

Partial-window business rates use the observed 5.155 seconds and exclude recovery. Acknowledged enrollment p95/p99 was 2,269.25/2,312.00 ms. Withdrawal p95/p99 was 2,240.40/2,297.80 ms. `dropped_iterations` was unavailable for this closed-model executor. The HTTP trace captured ten additional transport interruptions during cancellation, including one without matching edge ingress. They are included in reconciliation and account for the difference between 899 traced failures and 889 in the k6 phase counters.

The complete 30-second warm-up hold was already degraded. It recorded 481.03 RPS, p95/p99 of 2,061.97/3,978.36 ms, 533 HTTP 503s and 223 timeouts. Confirmed enrollments and withdrawals reached 76.30/s and 75.87/s. The first traced failure arrived 61.156 seconds after the ramp started, just after the target plateau began. The warm-up safety threshold evaluated the ramp and plateau together, so the earlier healthy ramp allowed the separate measurement phase to start. Measurement crossed the critical-error threshold on its own and stopped after the threshold's five-second delay. A future attempt needs a phase-local warm-up gate before measurement starts.

## Bottleneck evidence

| Signal | Sidecar 500 measurement | Sidecar 1,000 warm-up hold | Sidecar 1,000 partial measurement |
|---|---:|---:|---:|
| Startup JDBC `SELECT 1` p50/p95/p99 | 0.431 / 0.540 / 0.570 ms | 0.489 / 0.650 / 0.686 ms | Same startup probe |
| Hikari active / pending peak | 8 / 0 | 7 / 0 | 2 / 0 from one sample |
| Mean Hikari acquisition | 0.00019 ms | 0.046 ms | — |
| Section lock wait / hold | 0.406 / 2.529 ms | 0.497 / 3.172 ms | — |
| Application COMMIT | 0.403 ms | 0.510 ms | — |
| PostgreSQL connections / sampled lock waiters | 16 / 0 | 16 / 0 | 16 / 0 |
| Java process CPU peak / RSS | 0.945 cores / 743.78 MiB | 0.867 cores / 789.67 MiB | — / 814.79 MiB |
| k6 average CPU | 0.425 cores | 0.437 cores across ramp and warm-up | 1.082 cores |

The generator used 703.02 MiB of peak k6 RSS and 168.21 MiB of peak wrapper RSS against the 2 GiB limit. The available k6 CPU and memory metrics do not show sustained generator saturation. Cgroup CPU throttling and total peak cgroup memory were unavailable. Five PostgreSQL samples span the partial measurement. PostgreSQL WAL statistics over that sample window show 2,158 writes and syncs, 28.734 ms total write time, 6.962 ms total sync time and no WAL buffer fills. The sampled PostgreSQL process RSS sum peaked at 41.07 MiB. This sum is distinct from cgroup memory. The single Prometheus sample in measurement cannot establish JDBC or transaction timing deltas. Warm-up values above were recalculated from the saved time series for its final 30 seconds.

The measured bottleneck is HTTP upstream availability on the path behind the authenticated edge. The edge received all 666 traced HTTP 503s as upstream responses across warm-up and measurement. They affected reads and mutations. They carried neither Spring application error codes nor echoed correlation IDs. During the warm-up hold, 472 of 533 HTTP 503 responses completed between 1.980 and 2.100 seconds, close to HAProxy's 2-second upstream connect timeout. This supports an upstream connection or admission hypothesis. The run lacks the HAProxy termination flags, TLS connect timings and complete node ingress correlation required to distinguish HAProxy connection failures, Modal ingress limits and application listener admission. Sampled Hikari, PostgreSQL and JDBC latency show no corresponding saturation. Gateway budget rejection counts were zero. The next diagnostic should correlate those HTTP-hop signals and sample the edge cgroup, using the same PostgreSQL route and capacity settings.

## Durable outcomes and final state

The database export reconciled all 14,890 durable outcomes and the same number of audit rows. It found zero inconsistent or unmatched durable records. Of 14,899 mutation intents, 14,651 had an HTTP acknowledgement, 239 committed without one, and nine had no durable commit. Two timed-out requests were proved to have committed before the timeout. Another 74 timeout timestamps could not establish the ordering, while their durable outcomes were available. The ledger contains 368 retries and 308 individual mutation attempts without a matching response. The reconciliation gate records this missing HTTP evidence explicitly.

Bounded recovery resolved 16 of 16 warm-up cases and 64 of 312 measurement cases using the benchmark's original idempotency keys for replays. PostgreSQL retained 174 active enrollments after recovery, including 173 whose enrollment lacked an acknowledgement. All eight structural SQL checks passed and the exact final-state gate failed. No rows were manually deleted. The report retains the active rows' durable states and the sandbox was removed during cleanup.

## Cost, artifacts and cleanup

The preflight ceiling was $0.93295133 including reserve. Modal's tagged usage receipt attributes $0.20591798 before credits to the run, with $0.14668332 CPU, $0.04589668 memory and $0.01333798 network egress. The workspace billing summary returned $0 billed at receipt check time. The stop was verified with App state `stopped`, zero active Sandboxes and zero active containers. Only the one authorized 1,000-VU run was launched.

Performance Lab shows both runs under **Cloud → Phase 9 architecture experiments** and the Sidecar verdict in **Closed-model scaling qualification**. Run-local evidence is under `infrastructure/.local/modal/runs/modal-20261108T131646Z-ead379/` in `report.json`, `invariants.json`, `http-trace.json`, `operation-reconciliation.json`, and the k6, PostgreSQL and JVM sample files.
