# Mixed benchmark with JDBC and Redis sessions

## Reproduction

From the repository root, build the backend with JDK 21, then prepare the Modal assets. Use the authenticated Modal SDK 1.6.3 environment for all Modal commands.

```bash
(cd backend && ./gradlew --offline test bootJar)
python3 infrastructure/modal/prepare.py

STORE=jdbc
VUS=1000
python3 infrastructure/modal/benchmark.py plan --profile mixed --vus "$VUS" \
  --session-storage "$STORE" --http-experiment direct --transport encrypted-tunnel \
  --pool-size 16 --split-database --prefill-pool --spread-start \
  --ramp-seconds 60 --ramp-start-vus 1 --resource-profile a --private-db-sidecar \
  --repeat 1 --max-usd 1.50
```

After checking the plan, use the same arguments with `run` and `--confirm`. Set `STORE=redis` for arm B. Both arms run in `us-east` with 5,000 synthetic students, 20 distributed sections, 5,040 seats, the same artifact and seed, business Hikari pool 16, PostgreSQL commit delay zero and identical k6 settings. The 60-second ramp is followed by 30 seconds at target. The runner opens the 120-second measurement only after the warm-up gate. Each higher tier requires a complete, reconciled, stopped preceding tier using the same mode, workload and JAR.

To record the actual cost without modifying earlier reports, use `python3 infrastructure/modal/billing.py --date YYYY-MM-DD --run-id modal-YYYYMMDDTHHMMSSZ-abcdef`. Check the report cleanup state and `modal container list` before starting another run.

| Target VUs | Sandbox timeout | Generator timeout | Planned upper bound per run, before credits |
| --- | ---: | ---: | ---: |
| 1,000 | 728 s | 538 s | $0.964565 |
| 1,500 | 828 s | 638 s | $1.097232 |
| 2,000 | 928 s | 738 s | $1.269899 |
| 2,500 | 1,028 s | 838 s | $1.402567 |

The runner verifies workspace metered usage plus unsettled reservations and the next ceiling against the $25 project budget. Actual receipts are attributed by exact App and run tag. The pricing source is the authenticated workspace rate API. [Modal pricing](https://modal.com/pricing)

## Interpretation

A completed benchmark requires the configured VUs, a full 120-second window, p95 below 1,000 ms, p99 at or below 2,000 ms, zero unexpected responses and zero timeouts or interrupted iterations. The final check reconciles every durable enrollment and withdrawal, audit records and eight SQL invariants. A stopped warm-up may have clean structural SQL and complete durable reconciliation, while the overall SQL/HTTP benchmark gate is failed because the required measurement is absent.

Compare steady-state RPS and percentiles only across complete windows. Compare target-hold metrics separately for a run stopped in warm-up. Each run uses fresh infrastructure, so the exact worker host is uncontrolled. PostgreSQL session statement totals are top-level runtime calls to the Spring Session tables from `pg_stat_statements`, including actor preparation and all load phases. These counts represent SQL executions. The driver-level BEGIN/COMMIT exchanges and protocol packets are outside that count. JDBC checkout/hold totals are differences across the resource sample interval. Redis command and repository percentiles are histogram bucket upper bounds. INFO operations/s includes local observation commands.

Performance Lab displays sanitized reports in **Cloud → JDBC and Redis session comparison**. Raw k6 output, PostgreSQL evidence, Redis/Prometheus samples, invariants and durable reconciliation are retained in the corresponding `infrastructure/.local/modal/runs/<run-id>/` directory. Reports from earlier architecture experiments retain their original meaning.

## Early attempts

| Run ID | Store / target | Result |
| --- | --- | --- |
| `modal-20261108T160034Z-f7497c` | JDBC / 1,000 | Warm-up failed at 946.00 RPS, p95 1,263.73 ms and p99 1,525.71 ms. There were 60 timeouts in the 30-second target hold and 107 across warm-up including ramp and drain. All 21,344 durable outcomes were reconciled. The eight structural checks passed and active enrollments were zero. |
| `modal-20261108T160618Z-449876` | Redis / 1,000 planned | Startup failed because Redis 7.0.15 rejected the Redis 7.2 `CLIENT SETINFO` ACL entry. |
| `modal-20261108T160832Z-3944be` | Redis / 1,000 planned | The Redis process was ready, but the direct gateway preflight returned 503 on CSRF session creation. The pinned Spring Data Redis adapter writes with `HMSET`. The scoped ACL now permits that command. |
| `modal-20261108T161348Z-0748ca` | Redis / 1,000 | The 120-second HTTP window achieved 1,199.22 RPS, p95 16.56 ms and p99 17.97 ms with zero errors. SQL showed 71,974 outcomes, zero active enrollments, zero structural violations and zero JDBC session statements. Modal terminated the operation-ledger export, so the complete run gate failed. |
| `modal-20261108T162327Z-4bcd2a` | Redis / 1,000 | Evidence export completed. Warm-up failed with 19 client timeouts, 1,193.37 RPS, p95 440.57 ms and p99 563.02 ms. PostgreSQL had zero session statements, zero active enrollments and zero structural violations. All 23,924 durable operations were reconciled. |
| `modal-20261108T162933Z-fe2ec7` | Redis / 1,000 | The 120-second HTTP window achieved 1,199.24 RPS, p95 12.74 ms and p99 18.51 ms with zero errors. Modal timed out reading the backend sample export. The complete gate remained failed. |

After the two export failures, the runner switched from exec stdout to the Modal SDK filesystem API for compressed telemetry and PostgreSQL operation evidence. It verifies a 1 MiB fixture and checksum on both roles before starting the generator. A failed telemetry capture now permits the independent SQL reconciliation attempt while keeping the incomplete run ineligible for a pass. [Modal Sandbox filesystem](https://modal.com/docs/guide/sandbox-files)

## Matched 1,000-VU target holds

| Metric | JDBC `f7497c` | Redis `4bcd2a` |
| --- | ---: | ---: |
| HTTP RPS | 946.00 | 1,193.37 |
| p95 / p99 | 1,263.73 / 1,525.71 ms | 440.57 / 563.02 ms |
| Client timeouts in target hold | 60 | 19 |
| Hikari peak active / pending | 16 / 64 | 16 / 60 |
| Hikari mean acquisition | 15.34 ms | 13.14 ms |
| Tomcat peak busy / configured | 80 / 80 | 80 / 80 |
| Mean section lock hold | 6.54 ms | 7.14 ms |
| Full-run SQL statements against Spring Session tables | 172,509 | 0 |

In the JDBC hold sample interval, 36,857 session reads and 24,577 session writes held connections for 88.016 and 53.501 connection-seconds respectively. The intervals covered 25.95 seconds for JDBC and 26.12 seconds for Redis. These values are resource sample deltas inside the 30-second hold and are distinct from the full-run SQL totals.

The Redis timeout burst occurred after the direct benchmark gateway accepted the requests. The trace joined all 19 at the node gateway and showed cancellations around the four-second k6 deadline. Fourteen requests started within 311 ms. During the burst, sampled Tomcat busy threads were 80, Hikari pending reached 60, and there were no sampled listen drops or overflows. The generator averaged 0.70 CPU cores against its four-core quota. Redis showed zero errors and evictions, two peak clients, 3.63 MiB peak used memory and 30.80 MiB RSS. Command p95/p99 bucket upper bounds in the hold were 1.40/4.19 ms. These observations identify residual application/JDBC contention. The exact service step responsible for each timed-out request is unresolved.

The 19 failures included 18 reads and one enrollment request. A same-key retry committed the enrollment once. There were zero unresolved durable outcomes. The earlier complete HTTP window acknowledged 23,991 enrollments and 23,991 withdrawals, or 199.93 per second each. That run lost its full operation export, so these rates remain HTTP evidence.

## Final gate and cost

`modal-20261108T164145Z-719c2c` passed the 1 MiB checksum probe on the application Sandbox and PostgreSQL Sidecar. The local Modal client then reported a DNS resolution error during the generator call and the remote input was cancelled. The run produced no usable k6 or reconciliation artifact.

The Redis arm has two clean 120-second HTTP windows at 1,000 VUs and a separate full reconciliation of a failed warm-up. A clean measurement and complete reconciliation were never observed together in one Redis run. The 1,500-, 2,000- and 2,500-VU session benchmarks are Not Tested. The prior qualified 1,000-VU JDBC benchmark remains the project's verified mixed capacity result for this topology.

**Decision: Keep as an opt-in experiment.** Redis eliminated the observed SQL session workload and improved the paired target-hold latency and throughput. Tomcat reached 80 busy threads in both paired holds and the residual Hikari queue reached 60. The default remains JDBC pending a reproducible complete Redis HTTP and SQL pass.

| Run ID | Modal-metered USD before credits |
| --- | ---: |
| `modal-20261108T160034Z-f7497c` | 0.23920055 |
| `modal-20261108T160618Z-449876` | 0.00627342 |
| `modal-20261108T160832Z-3944be` | 0.06880819 |
| `modal-20261108T161348Z-0748ca` | 0.48263555 |
| `modal-20261108T162327Z-4bcd2a` | 0.24097994 |
| `modal-20261108T162933Z-fe2ec7` | 0.44198153 |
| `modal-20261108T164145Z-719c2c` | 0.34619623 |
| **Total** | **1.82607541** |

Modal reported zero billed USD after workspace credits and allowances. All seven Apps were verified stopped with zero active Sandboxes or containers. The workspace container listing was empty.

## Regression evidence

The JVM suite passed 102 tests, including real Redis 7.0.15 with the benchmark ACL, session fixation, expiry, revocation, outage, restart, cross-instance lookup and durable replay. The separate PostgreSQL correctness stress test returned exactly 30 enrollments and 1,970 full-section denials for 2,000 attempts. Both Student Portal Playwright cases and all nine Performance Lab Playwright cases passed. The Modal Python suite passed 26 tests. The Go gateway suite and frontend production build passed. All 100 pre-existing benchmark evidence checksums matched.

## Recoverable evidence transport

Session runs submit the restricted generator once with Modal `Function.spawn()` and save its FunctionCall ID before polling. A DNS or transient connection failure during polling retries the same call. The worker records a start marker before running k6, commits an atomic result and SHA-256 manifest to a per-run artifact Volume, and returns only the small manifest. The operator validates the artifact size and digest. An ambiguous submission is kept as a failed run. The operator only retries preflight DNS and evidence reads.

The artifact Volume is mounted only by the generator. PostgreSQL, Redis and their data still use the temporary Sandbox filesystem. The operator reads immutable binary pages for larger Sandbox and PostgreSQL evidence, validates exact lengths and checks the gzip integrity before using the data. A transfer error remains a failed qualification even if the HTTP result is clean.

The runner deletes the named artifact Volume after the App stops and verifies its absence. If the operator process is killed before cleanup, use the exact Volume name in the run's `report.json` after confirming the App is stopped.

```bash
modal volume delete <evidence_volume_name> --allow-missing --yes
```

A higher session tier requires a verified 120-second HTTP window at the immediately preceding tier, a successful warm-up, full SQL invariants and durable reconciliation, a stopped App with zero active compute, and verified Volume cleanup. [Modal Function invocation](https://modal.com/docs/guide/function-invocation-methods) · [Modal Volumes](https://modal.com/docs/guide/volumes)

The first cloud integration attempt exposed a missing import and the SDK's built-in timeout for a pending result. The runner now tests the checkpoint writer independently and treats a pending poll as a normal result state. The App and its Volume were removed after the failed attempt.

In `modal-20261108T170433Z-0270f1`, the new transport retrieved and verified the k6 checkpoint, backend and PostgreSQL samples, and the operation ledger. The 1,000-VU hold achieved 1,199.07 RPS, p95 202.43 ms and p99 304.17 ms. Two schedule requests and one sections request timed out after gateway ingress, so the 120-second measurement stayed closed. The reconciliation matched all 23,992 durable operations, all eight structural invariants passed and there were zero active enrollments. The App and its artifact Volume were removed.

The repeat `modal-20261108T171003Z-5c0abf` passed warm-up and held 1,000 VUs for 120 seconds. It achieved 1,197.42 HTTP RPS, p95 205.77 ms and p99 404.75 ms. The operator verified the 5,303,347-byte generator artifact by SHA-256 and saved the PostgreSQL operation ledger and resource samples. All 71,912 durable operations reconciled, the SQL gate passed and active enrollments were zero. There were 32 client timeouts in the measurement window, all on reads after gateway ingress. The HTTP gate failed, so 1,500, 2,000 and 2,500 VUs stayed untested.

The three transport validation Apps and their named evidence Volumes were removed. The separate 2 MiB restricted-worker probe also stopped. Modal attributed USD 0.81404701 before credits to these Apps and the probe.

## Tomcat upstream connection reuse

In `modal-20261108T171003Z-5c0abf`, 31 of the 32 read timeouts started between seconds 94.3 and 99.5 of measurement. Both the edge and the node gateway accepted those requests. During the burst Tomcat reached 80 busy threads and HikariCP had 60 pending acquisitions. The listener queue stayed empty, with zero sampled listen drops. Sandbox-wide TCP retransmissions rose by 443 between the 94.6-second and 99.6-second snapshots. The TCP counters aggregate all Sandbox traffic.

The node gateway used Go's default HTTP transport to Tomcat, which retains only two idle connections per host. A two-wave local test at concurrency 16 reproduces 30 TCP connections with that default and 16 with a pool large enough to retain the first wave. The direct benchmark route now retains up to 256 idle upstream connections. It records aggregate fresh and reused acquisitions and, on an upstream failure, the time to acquire a connection and write the request. The outer TLS route, the Redis session mode, the JDBC pool and the PostgreSQL Sidecar are preserved. [Go HTTP Transport](https://pkg.go.dev/net/http#Transport)

The corrected 1,000-VU run `modal-20261108T174806Z-51989e` passed. Its 120-second window reached 1,198.98 RPS, p95 29.11 ms and p99 32.65 ms with zero unexpected results. The node transport recorded 140,933 reused and 711 fresh acquisitions over 118 sampled seconds. Sandbox-wide retransmissions fell from 2,444 to one in the matched 119-second counter intervals. Peak Tomcat busy threads and Hikari pending acquisitions fell from 80 and 63 to 15 and zero. PostgreSQL reconciled all 71,958 durable operations, and App, Sandbox and Volume cleanup passed before the 1,500-VU run started.

The 1,500-VU attempt `modal-20261108T175624Z-707fa4` reached its 30-second target hold without HTTP errors. It measured 1,501.57 RPS, p95 1,773.15 ms and p99 2,490.61 ms. Latency and iteration pacing failed the warm-up gate, so the 120-second measurement remained closed. Tomcat reached 80 busy threads, and HikariCP peaked at 16 active connections and 64 pending acquisitions. The structural SQL checks passed, all 33,348 durable outcomes reconciled, and zero enrollments remained active. Both Apps and their artifact Volumes were removed. The 2,000- and 2,500-VU tiers stayed untested.

## Shared Sandbox CPU headroom

The 9-core 1,500-VU hold had a repeating gap in PostgreSQL activity. Only 1.71% of commits and 1.69% of claims landed in UTC milliseconds 700–899 of each second. The longest gaps were 383 ms and 378 ms respectively. The local TLS probe took up to 496.34 ms. The clean 9-core 1,000-VU measurement had 20.85% of commits in the same phase, a 39 ms maximum commit gap, and a 4.07 ms maximum TLS probe. The 1,500-VU gaps affected independent work on the same Sandbox and coincided with a configured one-second CFS period. Direct throttling counters were unavailable. These observations motivated a CPU-headroom experiment. [Linux CFS bandwidth control](https://kernel.org/doc/html/v6.16/scheduler/sched-bwc.html)

The Redis-only `--sidecar-cpu-cores` option sets both the shared Sandbox CPU request and hard limit. The first experiment raised the default of 9 to 12. It preserves the 17 GiB memory limit, the 16-connection HikariCP pool, 80 Tomcat workers, Redis, PostgreSQL Sidecar, build, fixture and workload. Before load, the runner verifies the requested cgroup limit on the application and the Sidecar. The runner normally requires the preceding run to have the same shared CPU limit, the same artifacts and all HTTP, SQL, reconciliation and cleanup gates. [Modal resource configuration](https://modal.com/docs/guide/resources)

The first 12-core control `modal-20261108T181119Z-ac09c2` failed warm-up on a single section-listing timeout during the ramp. The gateway recorded the request 4,026 ms after the k6 start timestamp and returned HTTP 200 after 4 ms of local processing. The exact source of the delay before the gateway is unresolved. The 30-second target hold had no HTTP errors, p95 was 26.83 ms, Tomcat peaked at 11 busy threads and HikariCP had no pending acquisitions. All 23,970 durable outcomes reconciled. The repeat `modal-20261108T181831Z-ad08bf` passed the full 120-second gate at 1,199.29 RPS and p95/p99 of 12.45/13.74 ms with zero unexpected responses. All 71,968 durable outcomes reconciled. Both Apps and both evidence Volumes were removed before the 1,500-VU attempt.

| Matched 30-second hold at 1,500 VUs | 9 CPU `707fa4` | 12 CPU `b349f6` |
| --- | ---: | ---: |
| HTTP RPS | 1,501.57 | 1,799.43 |
| HTTP p95 / p99, ms | 1,773.15 / 2,490.61 | 13.22 / 14.64 |
| Offered iterations out of 36,000 | 30,594 | 35,989 |
| Tomcat peak busy / configured threads | 80 / 80 | 13 / 80 |
| HikariCP peak active / pending | 16 / 64 | 9 / 0 |
| HikariCP mean acquisition, ms | 19.36 | 0.002 |
| HikariCP mean checkout duration, ms | 5.06 | 1.59 |
| Mean section lock wait / hold, ms | 1.60 / 6.84 | 0.40 / 2.61 |
| Mean application-observed commit, ms | 1.27 | 0.42 |
| Maximum sampled local TLS probe, ms | 496.34 | 4.00 |
| Maximum interval between PostgreSQL commits, ms | 383 | 25 |
| PostgreSQL commits in UTC milliseconds 700–899 | 1.71% | 19.65% |

Application counters cover 26.36 and 25.45 seconds within the two holds. Both runs used identical application, gateway, PostgreSQL, workload and fixture artifacts. The benchmark infrastructure changed the shared CPU request and limit. The disappearance of the recurring stalls and queue saturation supports insufficient shared CPU headroom as the cause at 1,500 VUs. The underlying provider scheduling mechanism remains unverified.

| 12-core run | HTTP window and result | RPS | HTTP p95 / p99, ms | Unexpected HTTP outcomes | Durable operations reconciled | Modal-metered USD before credits |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| `modal-20261108T181119Z-ac09c2` · 1,000 VUs | 30-second hold, ramp gate failed | 1,196.67 | 26.83 / 61.43 | 1 in ramp | 23,970 | 0.30699737 |
| `modal-20261108T181831Z-ad08bf` · 1,000 VUs | 120 seconds, passed | 1,199.29 | 12.45 / 13.74 | 0 | 71,968 | 0.50486937 |
| `modal-20261108T182617Z-b349f6` · 1,500 VUs | 120 seconds, passed | 1,797.86 | 41.09 / 80.20 | 0 | 107,926 | 0.56743327 |
| `modal-20261108T183439Z-06876b` · 2,000 VUs | 30-second hold, latency and pacing gates failed | 1,741.13 | 1,510.98 / 1,623.99 | 0 | 39,504 | 0.38761043 |
| **Total** | | | | | | **1.76691044** |

The full 1,500-VU window acknowledged 35,970 enrollments and 35,970 withdrawals, or 299.75 per second each. Peak Tomcat usage was 72 of 80 threads. HikariCP reached 50 pending acquisitions, with a window mean acquisition of 1.89 ms. Mean section lock hold was 2.84 ms and the maximum sampled local TLS probe was 5.25 ms. The 120-second gate, all eight structural SQL checks, durable reconciliation and evidence Volume cleanup passed before the 2,000-VU App started.

The 2,000-VU hold started 34,933 of 48,000 planned iterations and did not enter the 120-second measurement. In its hold, Tomcat reached 80 busy threads, HikariCP reached 63 pending acquisitions, and mean connection acquisition and checkout times rose to 17.87 ms and 4.39 ms. Mean section lock hold was 5.75 ms. PostgreSQL had up to four sampled lock waiters and gaps of up to 362 ms between commits. The local TLS probe stayed below 10 ms and 13.55% of commits landed in the 700–899 ms phase. This run did not reproduce the pronounced one-second scheduling pattern seen in the 9-core 1,500-VU run. It establishes application/JDBC queueing under this load and leaves the underlying limit unresolved. The generator averaged 1.03 of its four CPU cores across warm-up and peaked at 1,456 MiB RSS out of its 4,096 MiB limit. Direct throttling counters were unavailable.

All four runs passed the structural SQL checks and ended with zero active enrollments. Modal verified four stopped Apps and deletion of all four evidence Volumes. The workspace had zero active containers and zero remaining benchmark evidence Volumes. Modal reported zero billed USD for the workspace after credits and allowances. The highest qualified Redis Sidecar tier on this profile is 1,500 VUs.

## 16-core Redis Sidecar retest

`--sidecar-cpu-cores 16` repeats the 2,000-VU workload on the same TLS route, build, fixture, application settings and generator limits. The runner accepts the fully qualified 12-core 1,500-VU run as a predecessor for this resource-only retest. The 2,500-VU tier still requires a 2,000-VU pass on the same 16-core configuration, including the full 120-second HTTP gate, SQL invariants, durable operation reconciliation, stopped App and verified evidence Volume deletion. The preflight ceilings with current workspace rates were USD 1.72 for 2,000 VUs and USD 1.90 for 2,500 VUs before credits.

| Matched 30-second hold at 2,000 VUs | 12 CPU `06876b` | 16 CPU `7fd996` |
| --- | ---: | ---: |
| HTTP RPS | 1,741.13 | 1,775.23 |
| HTTP p95 / p99, ms | 1,510.98 / 1,623.99 | 1,410.81 / 1,476.14 |
| Iterations started out of 48,000 | 34,933 | 35,595 |
| Unexpected HTTP outcomes and interrupted iterations | 0 | 0 |
| Tomcat peak busy / configured threads | 80 / 80 | 80 / 80 |
| HikariCP peak active / pending | 16 / 63 | 16 / 60 |
| HikariCP mean acquisition, ms | 17.87 | 17.01 |
| Mean section lock wait / hold, ms | 1.44 / 5.75 | 1.42 / 6.11 |
| Peak sampled PostgreSQL lock waiters | 4 | 2 |

Modal verified a 16-core cgroup quota and 17 GiB memory limit on the application, gateway and PostgreSQL Sidecar. Application counters cover 26.12 and 20.95 seconds within the two holds. At 16 CPU, the local TLS probe stayed below 8 ms and the generator averaged 1.33 of its four cores during warm-up, with a peak RSS of 1,494 MiB. Direct throttling counters were unavailable. The larger quota improved latency and pacing modestly. Tomcat and HikariCP still saturated. The evidence leaves the underlying 2,000-VU limit unresolved.

`modal-20261108T184616Z-7fd996` failed the latency and iteration pacing checks before the 120-second measurement. All eight structural SQL checks passed and all 40,820 durable operations reconciled, including 20,410 enrollments and 20,410 withdrawals. There were zero active enrollments and no mismatches. Full qualification stayed closed because the measurement did not run. Modal metered USD 0.55781484 before credits and reported zero workspace billing after credits and allowances. The App stopped, zero Sandboxes and containers remained, and evidence Volume deletion was verified. The 2,500-VU run did not start.

## JDBC checkout capacity at 16 CPU

The HTTP and PostgreSQL ledgers from `7fd996` were joined by correlation ID. In the 2,000-VU hold, enrollment request p95 was 1,416 ms by the client ledger. The p95 from HTTP start to the durable claim timestamp was 1,372 ms, compared with 19 ms from claim to completed outcome. Cross-host timestamps are approximate and the pre-claim interval includes transport, servlet dispatch, session lookup, account validation and transaction setup. A sustained 80 busy Tomcat threads and 53–60 sampled HikariCP waiters show that queueing preceded most of the transaction work. The mean pool checkout wait was 17.01 ms across the 20.95-second counter interval, a narrower stage than the complete pre-claim interval.

In that interval, HikariCP completed 3,268 checkouts per second with a mean hold of 4.28 ms, equivalent to approximately 14 concurrently held connections on average. The workload targets 2,400 HTTP RPS. At the observed ratio of about 1.84 checkouts per HTTP request and the same mean hold, it would require about 19 concurrent connections on average. This projection motivates a controlled test with 24 prefilled connections. It assumes the same checkout cost under higher throughput. It does not establish that the extra connections will eliminate periodic stalls or prevent contention in PostgreSQL. [HikariCP pool sizing](https://github.com/brettwooldridge/HikariCP/wiki/About-Pool-Sizing)

The runner limits the 24-connection option to the Redis direct TLS Sidecar at 16 CPU and the 2,000- and 2,500-VU tiers. The comparison keeps the 80 Tomcat threads, PostgreSQL's 60-connection limit, the workload and the synthetic fixture. The 2,000-VU retest uses the already-qualified 1,500-VU predecessor with pool 16. The 2,500-VU run requires a complete 2,000-VU pass on the 24-connection profile and the same artifacts. The per-run ceilings remain USD 1.72 and USD 1.90 before credits.

| Matched 30-second hold at 2,000 VUs and 16 CPU | Pool 16 `7fd996` | Pool 24 `f6ee77` |
| --- | ---: | ---: |
| HTTP RPS | 1,775.23 | 2,329.07 |
| HTTP p95 / p99, ms | 1,410.81 / 1,476.14 | 210.34 / 515.55 |
| Iterations started out of 48,000 | 35,595 | 46,868 |
| Tomcat peak busy threads | 80 | 80 |
| HikariCP peak active / pending | 16 / 60 | 23 / 3 |
| HikariCP mean acquisition / checkout hold, ms | 17.01 / 4.28 | 0.31 / 1.57 |
| Mean section lock wait / hold, ms | 1.42 / 6.11 | 0.58 / 2.64 |
| Enrollment HTTP start to durable claim p95, ms | 1,372 | 119 |

The only workload configuration difference between these runs was the pool maximum and minimum idle count. The runs used the same backend, gateway, workload and fixture binaries. Counter intervals within the holds span 20.95 and 25.65 seconds respectively. The fixed-CPU comparison shows that the small JDBC pool was a material bottleneck. Tomcat still reached 80 busy threads during the hold and the evidence does not establish the provider's scheduling mechanism.

`modal-20261108T190216Z-f6ee77` passed the full 120-second mixed window at 2,000 VUs and 2,322.76 RPS, with p95/p99 of 219.78/612.23 ms and zero HTTP errors or interrupted iterations. HikariCP peaked at 21 pending with 1.36 ms mean acquisition. Tomcat peaked at 55 of 80 busy threads in the full window. The workload acknowledged 46,420 enrollments and 46,420 withdrawals, or 386.83 per second each. All 140,682 durable operations reconciled, all SQL checks passed, and zero enrollments remained active. The App stopped and the evidence Volume deletion was verified. Modal metered USD 0.74656601 before credits.

`modal-20261108T192750Z-65c764` started after the 2,000-VU HTTP and SQL checks, durable reconciliation, cleanup and the workspace budget check all passed. The 30-second hold at 2,500 VUs passed with 2,997.77 RPS, p95/p99 of 50.33/97.14 ms and zero unexpected outcomes. In the 120-second window, k6 observed 2,500 VUs and 2,709.08 HTTP RPS, with response p95/p99 of 787.68/875.61 ms. The full HTTP gate failed on 1,078 client timeouts in the window and 218,596 started iterations out of 240,000 planned. There were 1,118 client timeouts including drain. All reached gateway ingress and were recorded as canceled there. A traced sample of 500 also reached the node gateway. The gateway logged 502 while handling cancellation. k6 recorded client timeouts rather than HTTP 502 responses.

In the 2,500-VU measurement, Tomcat reached 80 busy threads and HikariCP reached 24 active and 54 pending connections. The mean checkout wait was 8.10 ms and mean section lock hold was 5.27 ms. PostgreSQL claims and completed outcomes again showed a repeating pause. Only 0.27% landed within UTC milliseconds 700–899, compared with 18.55% of completed outcomes in the 2,000-VU measurement. The 2,500-VU local TLS probe reached 175.49 ms. These signals show a wider stall than the JDBC checkout alone. Direct cgroup throttling counters were unavailable. k6 averaged 2.04 of its four cores and combined peak RSS for k6 and the worker was about 2.45 GiB of the 4 GiB limit.

PostgreSQL reconciled all 168,956 durable operations. The runner identified 272 retries, zero discrepancies and zero active enrollments. All structural and full-run SQL checks passed. The App stopped, all Sandboxes and containers exited, and the evidence Volume was deleted. Modal metered USD 0.73123835 before credits for this run and USD 1.47780436 for both pool-24 runs. The workspace billed total after credits and allowances was zero. The highest qualified mixed tier is 2,000 VUs.
