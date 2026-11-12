# JDBC contention isolation experiment

The latest historical direct-route 2,000-VU control passed at 2,392.78 HTTP RPS and 90.04 ms p95. A later single-backend HAProxy run reached 2,500 VUs for 30 seconds, produced 2,776.37 RPS at 756.59/860.23 ms p95/p99, and started 56,855 iterations against a 57,000 gate. It never entered the 120-second measurement. During 25.62 seconds of pool samples on the target hold, it completed 130,990 checkouts. Mean acquisition was 6.67 ms, usage 3.84 ms, pending peaked at 50 and Tomcat reached 80 busy threads. In the first saturation dump, 35 of 44 checkout waiters were in account validation. The `Accounts.valid` SQL itself averaged 0.010 ms on PostgreSQL. These observations motivate a test of pool contention across request classes. [Historical 2,000 VUs](../../infrastructure/.local/modal/runs/modal-20261108T204513Z-7f401d/report.json), [archived 2,500 VUs](../../infrastructure/.local/modal/runs/modal-20261109T080219Z-38cceb/report.json)

## One change per arm

| Arm | `--jdbc-security-pool` | Account-validation limit | Business limit | Total |
| --- | ---: | ---: | ---: | ---: |
| A | 0 | Shared | 24 | 24 |
| B1 | 4 | 4 | 20 | 24 |
| B2 | 6 | 6 | 18 | 24 |

Only `Accounts.valid` uses the new pool. The login credentials query and every business query and transaction continue to use Spring Boot's original DataSource and transaction manager. The additional `HikariDataSource` lives inside a benchmark-only component and is not registered as an injectable DataSource. It inherits the current JDBC route, timeouts and TLS settings, with pgJDBC `readOnlyMode=always` and Hikari `readOnly=true`. It uses the existing runtime role. The connection's read-only setting and the narrow component call path are separate from database-role permissions. Startup waits for the same combined 24 prefilled connections. Shutdown closes both pools.

Redis still loads the session. The filter still reads `enabled`, role, student ownership and `auth_version` from the PostgreSQL primary before CSRF and role authorization. A stale or disabled session fails before it can replay a durable mutation. No cache is involved. Application-side capacity, retries, lock ordering, audit, durable idempotency, Flyway, and the Student Portal flow remain unchanged. SQL tracing and the split are off in the default build. Switching back to the original runner arm uses `--jdbc-security-pool 0` with the same artifact.

## Local verification and transaction evidence

The HTTP regression uses actual PostgreSQL and Redis. With 24 borrowed shared-pool connections, authenticated `/me` returns 503 within the original bounded timeout. With every business connection borrowed, both split configurations return 200 for the same operation and return 401 immediately after `auth_version` changes. Enrollment still takes two checkouts, one from each pool in the split configuration. The test verifies PostgreSQL read-only mode, Redis login, enrollment, withdrawal and final invariants. It records a transaction JFR event and matches the correlation ID against the HTTP response. This demonstrates isolation under forced starvation. It does not measure sustainable business throughput.

Both cloud arms enable `sdu.EnrollmentTransaction` for attempts of 50 ms or more. It records a request correlation ID, operation, application SQL count, and monotonic start offsets and durations for transaction acquisition and setup, student and section locks, section hold, audit, durable outcome and commit. Transaction-control messages from the driver are outside the application SQL count. The Hikari acquisition timer measures actual pool acquisition separately. PostgreSQL snapshots report lock edges, WAL and business, security and operational client connection peaks. Operational connections include monitoring, migrations and test control activity and are reported outside the application's 24-connection limit. Performance Lab displays checkout counts and timings per pool for the measured phase, and distinguishes data from warm-up and measurement. Checkout ratios align cumulative API request and Hikari counters on the same scrape interval.

The archived 283.87 ms section lock wait was behind an open transaction whose last completed statement was `audit_write`. The PostgreSQL log records release after 285.970 ms. The last statement and aggregate `audit_write` times cannot identify the interval between that statement and COMMIT for this particular transaction. The recorded 263.23 ms young G1 evacuation pause occurred over 17 seconds later. [Prior lock and GC analysis](section-read-optimization.md#archived-saturation-and-separate-latency-events)

The new 2,000-VU control captured 980 transaction attempts above the 50 ms JFR threshold, including 564 enrollments with 20 application SQL calls each and 416 withdrawals with 13. At 09:24:26.61 UTC, JFR recorded 24 PostgreSQL socket reads of at least 250 ms in the same incident. Three independent transactions spent 318.01 ms on the post-lock decision clock, 318.32 ms writing a durable outcome, and 318.86 ms in the application COMMIT interval. The section lock remained held for up to 322.15 ms in the longest traced example. PostgreSQL's server-side decision clock query had a maximum of 0.07 ms, while its lock log recorded waits of 315.88 and 319.92 ms in the same interval. The longest GC pause in the measurement was 5.93 ms. The GC and safepoint events immediately before and after the incident do not overlap it. HAProxy's adjacent samples showed no backend queue. CPU pressure and throttling were unavailable. The delay is observable at the JDBC socket response boundary, but this recording cannot distinguish a delay in PostgreSQL scheduling from a delay in the private TLS transport. A targeted follow-up should timestamp receive and send boundaries on the private bridge and PostgreSQL side before changing SQL or JVM settings. [JFR](../../infrastructure/.local/modal/runs/modal-20261109T091816Z-a515c7/jvm-evidence-1.tar.gz), [PostgreSQL lock log](../../infrastructure/.local/modal/runs/modal-20261109T091816Z-a515c7/pg-lock-events.log), [statement timings](../../infrastructure/.local/modal/runs/modal-20261109T091816Z-a515c7/pg-stat-statements.csv)

## Matched cloud plan

Use one JVM with platform threads, one authenticated HAProxy route, Redis, PostgreSQL Sidecar with verified TLS, 16 CPU, 17 GiB RAM, Tomcat 80, fixed total JDBC budget 24, 5,000 synthetic students, unchanged fixture, workload and timeouts. A run uses the original 60-second ramp, 30-second target warm-up hold and 120-second measurement. By default each 2,500-VU mode requires its own passed and reconciled 2,000-VU result from the same build. For one explicitly confirmed 4+20 test at 2,500 VUs, `--shared-pool-control` can use the matched shared-pool result at 2,000 VUs. The runner records the control ID and applies the same warm-up, HTTP and SQL gates. Repeat the successful candidate independently. Keep failed attempts and raw evidence.

```bash
# Use Python 3.12 and the pinned Modal SDK environment.
mode=0
vus=2000
python infrastructure/modal/benchmark.py plan --profile mixed --vus "$vus" \
  --resource-profile a --private-db-sidecar --http-experiment direct \
  --transport encrypted-tunnel --pool-size 24 --prefill-pool --split-database \
  --ramp-seconds 60 --ramp-start-vus 1 --spread-start --repeat 1 \
  --session-storage redis --sidecar-cpu-cores 16 --jvm-threads platform \
  --hardware ha-one --tomcat-threads 80 --jdbc-security-pool "$mode" --max-usd 2.10
```

Compare overall HTTP RPS, endpoint p95/p99, acknowledged enrollments and withdrawals per second, checkout count and mean acquisition/usage per pool, Tomcat busy threads, lock hold, GC, wait events and all SQL invariants. Use the existing HTTP and SQL gates without overrides. A lower security-pool queue with a larger business queue is insufficient to retain a split.

The per-run ceilings are $1.7180 at 2,000 VUs and $1.8989 at 2,500 VUs, before credits. The initial three-arm 2,000-VU comparison and three 2,500-VU runs have a combined conservative ceiling of $10.8508. The runner compares metered workspace usage, outstanding reservations and the next run against the default $25 project ceiling before provisioning. A separately authorized project ceiling can be passed with `--approved-project-limit-usd` for exactly one confirmed run. The per-run ceiling, resource bounds and predecessor gate still apply. Each run saves the budget snapshot. Modal's summary exposes applied credits and billed cost. It does not expose a wallet or unused credit balance in this SDK. [Modal Workspace billing](https://modal.com/docs/sdk/py/latest/Workspace#billing)

## Results from November 9, 2026

| Arm | VUs | Window | HTTP RPS | p95 / p99 | Successful enrollments / withdrawals per second | Result |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| A, shared 24 | 2,000 | 120 s | 2,354.15 | 261.42 / 658.62 ms | 392.42 / 391.78 | Passed |
| B1, 4 + 20 | 2,000 | No Data | No Data | No Data | No Data | Not Tested |
| B2, 6 + 18 | 2,000 | No Data | No Data | No Data | No Data | Not Tested |
| A, shared 24 | 2,500 | 30 s warm-up hold | 2,715.23 | 769.24 / 935.75 ms | 454.47 / 449.33 | Failed warm-up pace gate |
| B1, 4 + 20 | 2,500 | 30 s warm-up hold | 2,489.37 | 1,074.57 / 1,248.44 ms | 415.17 / 402.53 | Failed warm-up gate |
| B2, 6 + 18 | 2,500 | No Data | No Data | No Data | No Data | Not Tested |

The shared control started 188,543 iterations against the 182,400 threshold, with zero unexpected HTTP outcomes, client timeouts or interrupted iterations. It reconciled all 142,408 durable operations across warm-up and measurement, passed all eight SQL invariants and left zero active enrollments. PostgreSQL sampled up to 24 application and two operational connections. The fixed-window pool sample span was 113.254 seconds. The shared pool completed 497,080 checkouts, approximately 1.82 per API request, with mean acquisition of 2.12 ms and mean usage of 2.31 ms. Peak pending was 51 and Tomcat reached 80 busy threads. The mean section lock wait and hold were 1.28 and 3.22 ms. PostgreSQL sampled `Lock:tuple`, `Lock:transactionid`, `Client:ClientRead`, and `IO:DataFileExtend`. These metrics are the control for the future matched comparison. [Cloud run and raw artifacts](../../infrastructure/.local/modal/runs/modal-20261109T091816Z-a515c7/report.json)

| Endpoint | p95 | p99 |
| --- | ---: | ---: |
| Sections | 289.32 ms | 730.77 ms |
| Schedule | 298.12 ms | 611.28 ms |
| Own enrollments | 262.96 ms | 580.74 ms |
| Me | 222.21 ms | 690.82 ms |
| Enroll | 239.00 ms | 712.21 ms |
| Withdraw | 215.00 ms | 735.88 ms |

Modal attributed $0.82380774 before credits to the control. The App stopped and cleanup verified zero active Sandboxes and containers and removal of the evidence Volume. At the post-run check, $23.1241 was metered and $1.3551 remained reserved. The next 2,000-VU arm would have brought the conservative projection to $26.1972 against the default $25. The split arms are on hold. The workspace summary reported $18.85 applied credits and $0 billed to date. [Control billing and cleanup](../../infrastructure/.local/modal/runs/modal-20261109T091816Z-a515c7/report.json)

**Release decision: keep the shared pool as the active configuration.** Pool separation passed the local correctness and forced-starvation tests and remains opt-in pending matched cloud results. The stable Phase 11 release JAR is retained separately as `backend-release-candidate-phase11.jar`. The exact shared/split experiment JAR is archived as `backend-jdbc-pool-experiment.jar` for subsequent measurements. The local 2,000-student correctness stress test allocated 30 seats, returned 1,970 durable `SECTION_FULL` outcomes and recorded zero technical errors or invariant violations.

### One authorized 2,500-VU shared control

The 2,000-VU shared control unlocked exactly one same-build shared-pool run at 2,500 VUs. Its budget snapshot projected $26.1682 in cumulative metered usage, reservations and this run's $1.8989 ceiling. The single-run cumulative cap was $27.00. The fixture, TLS route, 16 CPU, 17 GiB RAM, Tomcat 80, HikariCP 24, Redis, Sidecar and HAProxy were retained. The split arms still require their respective 2,000-VU controls. [2,500-VU report](../../infrastructure/.local/modal/runs/modal-20261109T093255Z-a91307/report.json)

The target hold reached 2,500 VUs for all 30 seconds. It started 54,855 iterations against the threshold of 57,000, a shortfall of 2,145. HTTP errors, client timeouts and interrupted iterations were zero. The runner did not open the 120-second measurement. The entire attempt reconciled 57,624 durable mutations. All eight structural SQL checks returned zero violations and no active enrollments remained after recovery. The full HTTP/SQL qualification remains failed. [Invariant check](../../infrastructure/.local/modal/runs/modal-20261109T093255Z-a91307/invariants.json)

In 25.502 seconds of pool samples on the hold, mean acquisition was 7.64 ms, mean usage 4.08 ms and peak pending 50. There were 128,822 checkouts, about 1.88 per API request. Tomcat reached 80 busy threads and HikariCP had 24 active connections. In the target-hold dump, 28 of 29 checkout waiters were in `Accounts.valid`. The previous 2,500-VU target hold produced 2,776.37 RPS, 56,855 iterations, 6.67 ms mean acquisition and peak pending 50. Both observations are failed warm-ups. Their difference does not measure a pool-split effect.

At 09:36:34.69 UTC, 20 JDBC socket reads on the private PostgreSQL path spent at least 250 ms waiting. PostgreSQL logged concurrent transaction lock waits of 293.58 and 294.81 ms. The longest traced section hold was 337.63 ms, including an application-side `audit_write` interval of 321.73 ms. A 3.89 ms G1 pause ended before the observed socket-read cluster. The evidence again locates a response stall on the JDBC path. It does not isolate whether the private transport or PostgreSQL scheduling caused it. [JFR and dumps](../../infrastructure/.local/modal/runs/modal-20261109T093255Z-a91307/jvm-evidence-1.tar.gz), [PostgreSQL lock log](../../infrastructure/.local/modal/runs/modal-20261109T093255Z-a91307/pg-lock-events.log)

Modal attributed $0.42456659 before credits. The App stopped, no benchmark Sandbox or container remained, and the temporary evidence Volume was removed. The verified capacity remains 2,000 mixed VUs. [Run billing and cleanup](../../infrastructure/.local/modal/runs/modal-20261109T093255Z-a91307/report.json)

### One authorized 2,500-VU 4+20 experiment

The single run used `--shared-pool-control` with the qualified shared-pool result `modal-20261109T091816Z-a515c7`. The runner verified the same build, fixture, route and load settings. The conservative project estimate was $28.0046 before provisioning, with a $28.25 cap for this run. [Run configuration and budget](../../infrastructure/.local/modal/runs/modal-20261109T094455Z-c9185b/report.json)

The 4+20 variant reached 2,500 VUs and started 51,249 iterations against the 57,000 threshold. The 30-second hold produced 2,489.37 HTTP RPS at 1,074.57 ms p95 and 1,248.44 ms p99. Around the end of the hold, the backend returned 510 HTTP 503 `SERVICE_OVERLOADED` responses across 158 enrollment and 352 withdrawal attempts. The hold pace, p95, full warm-up HTTP and recovery gates failed. The run stopped before the 120-second measurement. The same-build shared pool previously reached 54,855 iterations and 769.24 ms p95, with zero unexpected HTTP outcomes, and also failed its warm-up gate. [HTTP traces](../../infrastructure/.local/modal/runs/modal-20261109T094455Z-c9185b/http-trace.json)

The security pool peaked at two active connections out of four, with zero sampled pending requests and a mean checkout wait of 1.04 ms. The business pool reached 20 active connections out of 20, with a peak of 50 pending requests and a mean checkout wait of 17.94 ms. The previous shared-pool hold had a mean checkout wait of 7.64 ms. This result supports keeping the shared pool for the tested workload. [Pool samples](../../infrastructure/.local/modal/runs/modal-20261109T094455Z-c9185b/samples.tsv)

The runner reconciled 55,837 durable mutations without inconsistencies. All eight structural SQL checks returned zero violations. Recovery left 73 active test enrollments, so the final-state check failed. Modal attributed $0.68080314 before credits. Cleanup verified that the App stopped and removed the temporary resources, including the evidence Volume. The verified capacity remains 2,000 mixed VUs. [SQL checks](../../infrastructure/.local/modal/runs/modal-20261109T094455Z-c9185b/invariants.json), [billing and cleanup](../../infrastructure/.local/modal/runs/modal-20261109T094455Z-c9185b/report.json)
