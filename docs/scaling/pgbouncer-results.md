# Phase 9 — PgBouncer

## Controlled comparison

Use the Phase 8 release candidate preserved locally as `sdu-rc-phase8` and the Phase 9 benchmark JAR with the narrowly scoped JDBC checkout fix described below. The Modal runs use one application and one PostgreSQL 18.6 instance in the same 1.5-core, 2 GiB Sandbox. The separate generator is limited to one core and 1 GiB. All traffic uses the same authenticated TLS benchmark gateway in `us-east`. Hikari has 16 connections with all 16 prefilled. The seed contains 500 synthetic students and twenty 32-seat sections. The benchmark uses a 60-second ramp from one VU, a 30-second full-level warm-up, a 120-second measurement and a 1.25-second minimum iteration period. The primary uses synchronous commit, fsync and full-page writes, with `commit_delay=0`.

PgBouncer 1.26.1 is built from the upstream release using its pinned SHA-256. The pooler listens on the Sandbox loopback, with SCRAM authentication and a distinct statistics principal. The application user's SCRAM verifier matches the primary's verifier. The client limit is 24, the server and database limits are 16, and there is no reserve pool. The queue timeout is 15 seconds. The server resets session-mode connections with `DISCARD ALL`. Transaction pooling tracks up to 128 protocol-level prepared statements per server. The primary connection still carries Flyway credentials on its direct URL. The pooler uses runtime credentials only. Raw `SHOW POOLS` and `SHOW STATS` are retained as local measurement evidence. [Release and hashes](https://www.pgbouncer.org/downloads/) · [Pooler options](https://www.pgbouncer.org/config).

## Compatibility and fault injection

The project uses Spring JDBC, HikariCP, pgJDBC and Spring Session JDBC. Hibernate and Spring Data JPA are absent from the dependency graph, so their compatibility is Not Tested. The application uses schema-qualified SQL and transaction-local timeout settings. Flyway's session-level role changes execute directly on primary. The transaction-pool configuration is for this runtime SQL contract. SQL `PREPARE`, session-level locks and session state that requires pinning remain incompatible with this mode. [PgBouncer feature matrix](https://www.pgbouncer.org/features.html) · [Prepared statements FAQ](https://www.pgbouncer.org/faq.html).

The Java probe extracts the actual driver from the backend JAR and forces named statements with `prepareThreshold=1`. It proved pinned server sessions in session mode and release/reassignment of server connections in transaction mode. The same prepared statement returned correct parameters on the replacement server. Both modes preserved the transaction-local `lock_timeout`, SQLSTATE `55P03` while another transaction held the section row, lock release after COMMIT, and JDBC read-only transactions.

The first session-mode fault test shut down the pooler and exposed HTTP 500 for a startup socket EOF during JDBC acquisition. The fix treats that combination as transient, while keeping authentication, permission and invalid-protocol errors permanent. Both pooling modes subsequently returned 503 during the injected outage and recovered the same database-backed session after restart. Each mode passed five browser tests for this fault, HTTP lost-response recovery for POST and DELETE, administrator/CSRF authorization, contention for the last seat, and session expiry. The optional live-monitoring browser check was skipped in the disposable demo. The final JVM regression suite and the 2,000-student correctness stress passed.

## Reproduce

From the repository root, build the backend, prepare the existing Modal assets, then build the pooler bundle.

```sh
dir=infrastructure/.local/scaling/pgbouncer-amd64
mkdir -p "$dir"
docker build --platform linux/amd64 --target artifact --output "type=local,dest=$dir" infrastructure/scaling/pgbouncer
tar -czf infrastructure/.local/modal/assets/pgbouncer-linux-amd64.tar.gz -C "$dir" pgbouncer lib

SDU_DEMO_DATABASE_PATH=transaction SDU_DEMO_POOLER_VERIFY=1 bash infrastructure/student-demo.sh --with-portal

python infrastructure/modal/benchmark.py plan --profile mixed --vus 200 \
  --transport encrypted-tunnel --pool-size 16 --diagnose-mixed --prefill-pool \
  --spread-start --ramp-seconds 60 --ramp-start-vus 1 --database-path transaction --max-usd .20
```

Use `direct`, `session` or `transaction` with the same workload. Actual cloud execution also requires the authorized budget and `--confirm`. The operator controller starts only one App at a time and checks its exact teardown.

## Observed results

The first cloud session attempt failed before load. The bootstrap captured a SCRAM verifier and the existing Flyway callback subsequently rotated its salt. The cloud bootstrap now runs the migration CLI against the primary before capturing the verifier. The application then starts with its Flyway hook disabled for pooled modes. The initial transaction attempt was stopped while correcting this startup issue. Both attempts were cleaned up. The direct control and successful pooled reruns used the same backend JAR and timed workload.

| Database path at 200 VUs | HTTP RPS | p95 / p99 (ms) | Enroll/s | Withdraw/s | Warm-up / measured errors | Hikari pending | PG runtime connections | PgBouncer waiting / active servers |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| [Direct](../../infrastructure/.local/modal/runs/modal-20261108T103435Z-359e51/report.json) | 239.74 | 33.66 / 148.34 | 39.95 | 39.94 | 0 / 0 | 17 | 16 | — |
| [Session](../../infrastructure/.local/modal/runs/modal-20261108T104341Z-f00dd2/report.json) | 239.79 | 41.09 / 80.93 | 39.96 | 39.95 | 0 / 0 | 3 | 16 | 0 / 16 |
| [Transaction](../../infrastructure/.local/modal/runs/modal-20261108T104831Z-28fce2/report.json) | 239.81 | 20.12 / 43.39 | 39.97 | 39.96 | 0 / 0 | 0 | 16 | 0 / 7 |

All three passed SQL checks and ended with zero active enrollments. The transaction mode observed 373,589 named binds between the first and last pooler samples. It had fewer simultaneously active PostgreSQL servers in sampled snapshots, while peak runtime connections remained 16. Session and transaction modes added approximately 16.4 and 16.7 MiB peak process RSS for the pooler. Connection and CPU samples were gathered over the measurement window. Peaks from different timestamps are not additive.

| 200-VU path | Mean section wait / hold ms | Mean transaction ms | PostgreSQL CPU-s / peak process RSS sum MiB | Peak Java CPU cores / RSS MiB | Metered USD |
| --- | ---: | ---: | ---: | ---: | ---: |
| Direct | 0.14 / 1.17 | 3.16 | 14.29 / 143.99 | 0.27 / 457.57 | 0.04725199 |
| Session | 0.21 / 1.67 | 4.31 | 14.54 / 149.42 | 0.22 / 425.07 | 0.04782431 |
| Transaction | 0.30 / 2.18 | 5.91 | 17.12 / 576.78 | 0.33 / 475.15 | 0.04750678 |

No PostgreSQL lock waiter was sampled in these 200-VU windows. The PostgreSQL RSS sum can count shared pages more than once. Pooler CPU totaled 5.67 and 9.03 CPU-s for session and transaction. k6 mean CPU was 0.20–0.27 logical core equivalents in the full windows.

The qualified transaction route was tested at 500 target VUs. It reached 500 in warm-up and hit the unchanged safety stop after 520 unexpected outcomes. There was no 120-second measurement. Hikari pending peaked at 64, PgBouncer waiting clients at two, and section hold averaged 40.55 ms in the sampled warm-up. All 3,278 committed outcomes matched logged intents. Full reconciliation failed, with 165 mutation starts missing a completed response. Bounded recovery resolved 64 of 180 pending journeys and the last database snapshot contained 54 active episodes. All eight structural checks still passed. The temporary database was removed during verified cleanup. [500-target run](../../infrastructure/.local/modal/runs/modal-20261108T105423Z-fbe47b/report.json).

**Decision.** Keep direct as the default. Session pooling is compatible and reached similar paced throughput with the same connection count. Transaction pooling stays an opt-in experiment. The lower tail latency is from one 200-VU run. At the 500 target, application and transaction contention still crossed the safety gate.

Stage 1 metered USD 0.23429945 before credits across seven runs, including the early direct check, both startup attempts and the 500 target. All seven verified cleanup.
