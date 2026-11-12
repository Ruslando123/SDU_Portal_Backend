# Phase 12: JVM, SQL and resource experiments

The reference Redis Sidecar at 16 CPU, 17 GiB and 24 JDBC connections passed 2,000 mixed VUs at 2,322.76 RPS and HTTP p95 219.78 ms. The 2,500-VU run passed its 30-second hold and failed the 120-second gate with 1,078 client timeouts and 218,596 of 240,000 scheduled iterations. All 168,956 durable operations reconciled. In the measurement window, Tomcat reached 80 busy threads and HikariCP reached 54 pending acquisitions. Only 0.27% of PostgreSQL claims and completed outcomes fell within UTC milliseconds 700–899 of each second. The longest commit gap was 311 ms and a local TLS probe reached 175.49 ms. CPU throttling counters were unavailable, so this correlation does not identify a provider scheduling mechanism. [Reference 2,000 VUs](../../infrastructure/.local/modal/runs/modal-20261108T190216Z-f6ee77/report.json) · [Reference 2,500 VUs](../../infrastructure/.local/modal/runs/modal-20261108T192750Z-65c764/report.json)

## Controlled JVM comparison

The backend uses Spring Boot 4.1.2 and targets Java 21. The runner sets `SPRING_THREADS_VIRTUAL_ENABLED=true` for `--jvm-threads virtual` and `false` for `--jvm-threads platform`. It also enables Spring Boot keep-alive for both arms. Both share the 16-core Sandbox, 17 GiB memory, a 4 GiB maximum Java heap, four processors reported to the JVM, pool 24, the same dataset and the same direct verified TLS path. Tomcat's platform thread count is 80. Virtual threads use the JVM scheduler. The existing 64-command enrollment admission cap and the 24-connection JDBC pool bound mutation admission and concurrent database work. [Spring Boot virtual threads](https://docs.spring.io/spring-boot/reference/features/spring-application.html#features.spring-application.virtual-threads) · [JDK 21 virtual threads](https://docs.oracle.com/en/java/javase/21/core/virtual-threads.html)

For both arms, the benchmark-only filter samples active API requests and records the actual execution mode. It writes thread dumps, a bounded JFR with `jdk.VirtualThreadPinned` events of at least 1 ms, execution samples, monitor entries, GC and CPU load. These private artifacts contain class and stack evidence. Performance Lab exposes only the mode and aggregate counters. JFR summary counts cover the recorded run including setup and warm-up. The artifact contains event timestamps for window-specific analysis. Java 24 changes monitor pinning and is a separate JDK experiment. [OpenJDK JEP 491](https://openjdk.org/jeps/491)

## JDBC statement inventory

The table covers a fresh successful operation with an empty active schedule, which is the benchmark fixture at the start of a journey. It counts application SQL executions. Authentication takes its own checkout. The Spring transaction keeps one JDBC connection from acquisition through completion. The PostgreSQL driver can send transaction setup and the first statement together, so application statement counts and wire exchanges are distinct. Timings exported by `pg_stat_statements` cover server execution. Client timings in `transaction_breakdown` include network, wait and application work, and some intervals overlap. [PostgreSQL statement statistics](https://www.postgresql.org/docs/18/pgstatstatements.html)

| Ordered step | Enroll | Withdraw | In the mutation transaction |
| --- | ---: | ---: | --- |
| Validate the authenticated account | 1 | 1 | Before checkout |
| Set local lock and statement deadlines | 1 | 1 | Yes |
| Claim idempotency key with `INSERT ... ON CONFLICT DO NOTHING` | 1 | 1 | Yes |
| Read section hint or owned enrollment | 1 | 1 | Yes |
| Acquire the term policy gate | 1 | 1 | Yes |
| Acquire the student-term lock | 1 | 1 | Yes |
| Read credit policy | 1 | 0 | Yes |
| Read offering and prerequisites | 2 | 0 | Yes |
| Read pinned snapshot and completions | 2, or 1 with the joined read | 0 | Yes |
| Read active enrollments | 1 | 0 | Yes |
| Read credits for existing offerings, only if present | 0 | 0 | Yes |
| Read required meetings | 1 | 0 | Yes |
| Check offering eligibility | 1 | 0 | Yes |
| Recheck owned enrollment under the student lock | 0 | 1 | Yes |
| Read decision window | 1 | 1 | Yes |
| Acquire section lock | 1 | 1 | Yes |
| Read the decision clock after locking | 1 | 1 | Yes |
| Write seat counter and enrollment episode | 2 | 2 | Yes |
| Write audit event | 1 | 1 | Yes |
| Finalize durable outcome with `UPDATE ... RETURNING` | 1 | 1 | Yes |
| **Application SQL executions including auth** | **21, or 20 with the join** | **14** | |
| Transaction protocol boundaries | `BEGIN` and `COMMIT` | `BEGIN` and `COMMIT` | |

The existing conditional seat update stays in place. The withdrawal recheck protects against concurrent state changes between reading the hint and acquiring the student-term lock. A conflicting idempotency insert can wait for the winner, so the replay path needs a separate statement with a fresh READ COMMITTED snapshot. Audit and outcome persistence stay in the same transaction. Batching dependent writes or placing them in PL/pgSQL would require new evidence and correctness checks. This phase tests a narrower hypothesis: joining the ownership-checked pinned snapshot to its completions saves one execution before the section lock. A PostgreSQL integration test checks matching evidence for empty and nonempty completions, ownership rejection and a reduction from two JDBC queries to one.

The platform control's statement timings are in [pg-stat-statements.csv](../../infrastructure/.local/modal/runs/modal-20261108T204513Z-7f401d/pg-stat-statements.csv). It measured 0.011 ms mean server execution for each pinned snapshot lookup and 0.005 ms for each completion lookup, with 71,864 calls each. These server timings exclude the avoided JDBC trip. The idle JDBC route probe median was 0.290 ms. The whole academic block averaged 3.059 ms on the application side. The mean connection duration after entering the transaction was 10.242 ms for enrollment and 7.445 ms for withdrawal.

| Measured mutation step | PostgreSQL mean execution, ms | Application mean, ms |
| --- | ---: | ---: |
| Idempotency claim | 0.110 | 0.508 |
| Student-term lock | 0.039 | 0.413 |
| Section lock acquisition | 0.936 | 1.772 |
| Decision window | 0.010 | 0.387 |
| Decision clock | 0.002 | 0.366 |
| Seat increment / decrement | 0.048 / 0.047 | 0.430 combined |
| Episode insert / withdrawal update | 0.106 / 0.096 | 0.491 combined |
| Audit write | 0.085 | 0.470 |
| Idempotency completion | 0.176 | 0.608 |
| Commit | No directly comparable server sample | 0.462 |

Mean section lock hold was 2.839 ms. The after-lock sequence includes the clock check, seat and episode updates, audit and durable result writes, and commit. The server statistics cover the full run, while the application means use the 120-second measurement window. The two columns are complementary measurements and should not be subtracted to estimate per-query network time.

## Hardware and arrival-rate profiles

Run `plan` before `run`. The same runner enforces the workspace reserve, fixture isolation, reconciliation and cleanup. The following arguments select the matched base profile. Append an experiment option to both the plan and the run. Use `run` with `--confirm` only inside the approved budget.

```bash
python3.12 infrastructure/modal/benchmark.py plan --profile mixed --vus 2000 \
  --resource-profile a --private-db-sidecar --http-experiment direct \
  --transport encrypted-tunnel --pool-size 24 --prefill-pool --split-database \
  --ramp-seconds 60 --ramp-start-vus 1 --spread-start --repeat 1 \
  --session-storage redis --sidecar-cpu-cores 16 --jvm-threads platform --max-usd 2.50
```

| Experiment | Change |
| --- | --- |
| Virtual threads | Replace `--jvm-threads platform` with `--jvm-threads virtual`. |
| Joined SQL | Add `--joined-academic-reads` to the platform control. |
| More resources | Add `--hardware larger` to request 20 CPU and 21 GiB in the shared Sandbox. The heap and pool remain 4 GiB and 24. |
| Equal-budget one vs two | Compare `--hardware ha-one` and `--hardware ha-two`. Both use the same local HAProxy listener behind the TLS gateway and one PostgreSQL Sidecar. Aggregate budgets are 16 CPU, 17 GiB, 4 GiB of Java heap, 24 prefilled JDBC connections, 80 Tomcat workers and 64 admitted mutation commands. Two JVMs split the heap, worker, processor, admission and pool budgets evenly. Both JVMs use the same Redis session store. |
| Open mixed | Add `--arrival-rate 1400`. The measured phase uses `constant-arrival-rate`, 2,000 preallocated VUs and 120 seconds, with no pacing sleep. Its preceding warm-up uses the closed scenario. |

For the open workload, try separate runs at 1,200, 1,400, 1,600, 1,800 and 2,000 journeys per second, within the approved budget. A journey starts one read and every fourth journey performs an enrollment and withdrawal. Measure HTTP RPS and successful mutation rates separately from offered journeys. Stop on drops, unexpected outcomes, an SLO breach, insufficient generator headroom or SQL discrepancies. Verify the highest passing intensity with an independent repeat before claiming sustainable capacity. [k6 arrival rate allocation](https://grafana.com/docs/k6/latest/using-k6/scenarios/concepts/arrival-rate-vu-allocation/)

The earlier pool 16/24 comparison at 16 CPU established a checkout bottleneck at 2,000 VUs. Keep 24 in the new comparisons. Higher pool tests require a fresh PostgreSQL CPU, wait and transaction analysis. A closed 2,500-VU run requires a verified closed 2,000-VU run for the same thread mode, SQL option, resource profile and artifacts.

## Verification and runs

Java 21 platform regression passed, including the 2,000-student stress check. The full virtual-thread and joined-read regression suites each passed 105 tests including the same stress check. Each stress run confirmed 30 enrollments, 1,970 capacity denials and zero invariant violations. A live HTTP integration test verified the actual Tomcat request thread mode and the JFR and thread dump files. The 31 runner tests and the Performance Lab tests, typecheck and production build passed. `k6 inspect` confirmed the 1,400-journey open profile's executor, duration and VU allocation.

The instrumented platform control `modal-20261108T204513Z-7f401d` passed 2,000 VUs at 2,392.78 RPS with p95/p99 of 90.04/129.86 ms. During that window, 398.60 enrollments and 398.56 withdrawals were acknowledged per second. It recorded zero unexpected outcomes, 143,728 reconciled operations, all SQL checks and a verified cleanup. The HTTP filter counted 439,190 API calls on platform threads and zero virtual calls. JFR recorded zero pin events. The measured peak was 80 Tomcat workers and 50 HikariCP waiters. Modal metered USD 0.83921915 before credits.

| Matched 30-second target hold at 2,000 VUs | Platform `7f401d` | Virtual `f8481f` |
| --- | ---: | ---: |
| HTTP RPS | 2,398.93 | 2,089.93 |
| HTTP p95 / p99, ms | 33.95 / 35.88 | 592.08 / 1,113.69 |
| Unexpected responses in hold | 0 | 1,417 |
| Successful enrollments / withdrawals per second in hold | 399.90 / 399.90 | 221.27 / 220.37 |
| Peak sampled active servlet requests | 23 | 1,181 |
| Peak HikariCP pending | 0 | 923 |
| Mean HikariCP acquisition / checkout hold, ms | 0.001 / 1.560 | 46.196 / 4.884 |
| Mean section lock hold, ms | 2.558 | 7.452 |
| JFR pin events in hold | 0 | 0 |

The virtual run `modal-20261108T205539Z-f8481f` opened no measurement window. The servlet filter verified 141,273 calls on virtual threads and a peak of 1,192 active requests across the run. It recorded 2,929 unexpected 503 `SERVICE_OVERLOADED` responses including the ramp and drain, split into 1,676 enroll and 1,253 withdraw attempts. The existing enrollment admission limit rejected the overload. JFR recorded three pins outside the target hold. The dominant measured change was pressure on bounded checkout and mutation capacity after the platform executor's dispatch limit was replaced. A Java 24 comparison would address monitor pinning and does not address this observed failure.

All 35,468 durable outcomes in the virtual run reconciled and the eight structural checks passed. A total of 360 active enrollments remained after the bounded recovery because withdrawals did not all complete. This failed the final-state and full SQL gates. The synthetic PostgreSQL Sidecar and App stopped and the evidence Volume was deleted. Modal metered USD 0.43987013 before credits. Keep platform threads as the release default.

The joined SQL run `modal-20261108T210335Z-473243` passed the 2,000-VU full gate with 2,391.21 RPS, p95/p99 of 122.34/363.87 ms, zero unexpected HTTP outcomes, 143,744 reconciled operations and zero active enrollments. PostgreSQL recorded exactly 71,872 joined reads at 0.017 ms mean server time and zero separate snapshot or completion reads. The academic block fell from 3.059 to 2.976 ms mean on the application. Full transaction time and HTTP tail latency did not improve. One matched pair does not establish a SQL regression or a performance benefit. The experiment remains opt-in. Modal metered USD 0.70417911 before credits.

| Release decision | 2,000-VU evidence | 2,500-VU evidence | Decision |
| --- | --- | --- | --- |
| Platform control | Passed 120 seconds. 2,392.78 RPS, 90.04 / 129.86 ms p95 / p99. | Historical profile failed with 1,078 client timeouts. | Keep platform default. |
| Virtual threads on Java 21 | Warm-up failed. 2,089.93 RPS, 592.08 / 1,113.69 ms and 1,417 HTTP 503 responses in the hold. | Gate closed. | Revert for release. Keep the diagnostic switch. |
| Joined academic read | Passed 120 seconds. 2,391.21 RPS, 122.34 / 363.87 ms and one fewer SQL call per enrollment. | Budget gate closed. | Revert for release. Keep the SQL option for a controlled repeat. |
| Increased resources | Plan validated at 20 CPU / 21 GiB. Cloud result unavailable. | Not run. | Decision pending measurements. |
| One vs two backends | Fixed-budget plans and local HAProxy routing validated. Redis session and durable replay across instances passed integration testing. Cloud result unavailable. | Not run. | Decision pending measurements. |

| Full measurement | Platform control | Joined academic read |
| --- | ---: | ---: |
| Successful enrollments / withdrawals per second | 398.60 / 398.56 | 398.53 / 398.33 |
| Peak HikariCP active / pending | 24 / 50 | 23 / 50 |
| Mean HikariCP acquisition / checkout hold, ms | 2.08 / 2.10 | 2.93 / 2.46 |
| Mean section lock wait / hold, ms | 1.77 / 2.84 | 2.84 / 3.13 |
| Mean time in enrollment / withdrawal transaction, ms | 10.24 / 7.45 | 11.80 / 9.04 |
| Sampled PostgreSQL CPU-seconds / peak lock waiters | 329.98 / 10 | 343.30 / 10 |
| Peak Java CPU cores / RSS MiB | 3.30 / 942.18 | 3.33 / 932.04 |
| JVM GC pause total, ms | 501 | 525 |

The three Phase 12 Apps metered USD 1.98326839 in total. Workspace billing after credits and allowances was zero. All Apps stopped, all evidence Volumes were deleted, and the workspace listed no active containers. The latest reserve calculation including the next 2,000- or 2,500-VU run was USD 26.90 or USD 27.08 respectively against the USD 25 project cap. The SQL repeat, hardware comparisons and cloud open-model ladder remain pending. The release candidate JAR was preserved at SHA-256 `d23b2502df9d3653aa9be1a681dffdb6a70c8e07113ec97fc1c4f5cce932b443` in `infrastructure/.local/modal/assets/backend-release-candidate-phase11.jar`.
