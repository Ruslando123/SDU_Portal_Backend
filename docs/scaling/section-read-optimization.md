# Account validation and section read experiment

## Authentication and the JDBC boundary

Spring Session loads the security context from Redis. For requests under `/api/`, `AccountGuard` calls `Accounts.valid` once if the context contains an `AccountPrincipal`. `Accounts.valid` uses `Sql.one` and `JdbcTemplate` with no enclosing transaction. The template checks out one connection, reads the current account from the primary by principal ID and releases the connection. The guard checks `enabled` and matches the principal ID, student ID, role and authentication version against the session. It also enforces the eight-hour authenticated lifetime. Revocation clears the context, invalidates the session and returns 401. A transient database error fails closed with 503.

The guard runs before CSRF and before URL authorization. Anonymous login has no `Accounts.valid` call and loads the account separately through `UserDetailsService`. An already authenticated request to login, logout or CSRF still passes through the guard. `CurrentActor` and `CurrentActor.student()` read the validated security context in memory. Student-only authorization then gates the controller. Enrollment commands derive ownership from that context and open their own transaction inside the retry loop.

| Route with an authenticated Redis session | Guard checkouts | Business checkouts on the ordinary successful path |
| --- | ---: | ---: |
| `GET /api/v1/me` and `/api/v1/auth/me` | 1 | 0 |
| `GET /api/v1/courses/{id}/sections` | 1 | 1 |
| `GET /api/v1/me/schedule` | 1 | 1 |
| `GET /api/v1/me/enrollments` | 1 | 1 |
| `POST /api/v1/sections/{id}/enrollments` | 1 | 1 per transaction attempt |
| `DELETE /api/v1/enrollments/{id}` | 1 | 1 per transaction attempt |
| `GET /api/v1/terms` and `/api/v1/courses` | 1 | 1 for the primary read |

The Redis integration test measures the Hikari acquisition counter across each mixed workload route. It also checks that a rejected write without CSRF has only the guard checkout. Moving account validation into a controller's SQL would defer it past CSRF, authorization and MVC argument handling. A revoked account can currently receive 401 even for malformed input that MVC would otherwise reject with 400. Pre-reading protected data in the guard would move the data read ahead of Spring Security's role authorization. Keeping a connection through the servlet filter chain would widen its lifetime and change the transaction scope. The experiment leaves all of these boundaries in place. [Spring JDBC connection management](https://docs.spring.io/spring-framework/reference/data-access/jdbc/connections.html)

## Candidate and local verification

`portal.performance.joined-section-meetings=true` joins the paged published sections and their meetings in a single `LEFT JOIN` inside the existing read-only repeatable-read transaction. The page is selected before the join. The query preserves sections without meetings, meeting ordering, pagination, the missing-offering response, the optional instructor projection and the final observation timestamp. The default remains the original query path. A section with many meetings can repeat section fields in the joined result, so SQL statement count alone does not establish reduced connection hold or CPU time.

A real HTTP integration test with PostgreSQL and Redis observed **five to four application SQL statements and two to two Hikari acquisitions** for a populated section page. The count includes the live account check and excludes driver transaction-control messages. The test compares section and meeting order, pagination, empty pages and the optional instructor projection. It exercises anonymous access, account disable, authentication-version revocation, CSRF, durable enrollment replay and withdrawal. On the archived 2,500-VU warm-up hold, the section endpoint returned 14,137 successes (471.23/s). [Archived warm-up](../../infrastructure/.local/modal/runs/modal-20261109T080219Z-38cceb/report.json)

## Archived saturation and separate latency events

In the 25.621 seconds covered by Prometheus counter deltas inside the 30-second target hold, Hikari completed 130,990 checkouts (about 5,113/s). Mean acquisition was 6.67 ms and mean usage was 3.84 ms. Peak pending was 50 and Tomcat reached 80 busy workers. The corresponding means across ramp and hold were 2.89 ms and 2.55 ms. These are aggregate pool timings. The archive cannot attribute connection hold to each endpoint under concurrent load. The matched test runner now preserves the underlying acquisition and usage counter deltas as well as the averages. [Pool samples](../../infrastructure/.local/modal/runs/modal-20261109T080219Z-38cceb/samples.tsv)

PostgreSQL sampled a 283.87 ms `section_lock` wait by PID 121 on transaction 38408. The blocker, PID 127, was idle in an open transaction for 290.34 ms after its last `audit_write`; the SQL log records release after a 285.970 ms wait. `pg_stat_statements` recorded mean server execution for `audit_write` of 0.101 ms, with a 281.11 ms maximum across the run. The blocker's last statement and aggregate query times cannot resolve how long that specific transaction spent waiting for the application to send its next statement. The lock was held across audit and the durable outcome by design. The two nearest pool-saturation snapshots had no captured blocker. [PostgreSQL snapshots](../../infrastructure/.local/modal/runs/modal-20261109T080219Z-38cceb/pg-samples.tsv), [lock log](../../infrastructure/.local/modal/runs/modal-20261109T080219Z-38cceb/pg-lock-events.log), [statement timings](../../infrastructure/.local/modal/runs/modal-20261109T080219Z-38cceb/pg-stat-statements.csv)

JFR GC event 165 at 08:07:17.749 UTC reports `G1New`, `G1 Evacuation Pause`, with 263.232 ms of stop-the-world pause. It occurred about 0.99 seconds after the second pool-saturation marker and over 17 seconds after the recorded lock released. The archived JFR has no GC phase, heap-summary or safepoint synchronization events to attribute the long pause to a particular evacuation phase or to CPU scheduling. Future saturation recordings include heap, GC CPU, pause-phase and safepoint events on both arms. [Archived JFR](../../infrastructure/.local/modal/runs/modal-20261109T080219Z-38cceb/jvm-evidence-1.tar.gz), [JDK 21 G1 documentation](https://docs.oracle.com/en/java/javase/21/gctuning/garbage-first-g1-garbage-collector1.html)

## Matched cloud comparison and decision

| Metric | Historical 2,000 VUs, direct TLS | Archived 2,500 VUs, HAProxy | Candidate 2,000 / 2,500 VUs |
| --- | ---: | ---: | --- |
| Window and status | 120 s, passed | 30 s target hold, pace gate failed | Not Tested |
| HTTP RPS | 2,322.76 | 2,776.37 | No Data |
| p95 / p99 | 219.78 / 612.23 ms | 756.59 / 860.23 ms | No Data |
| Successful enrollments / withdrawals per second | 386.68 / 386.60 | 459.93 / 450.33 | No Data |
| Hikari acquisition mean | 1.36 ms | 6.67 ms in the target hold sample span | No Data |
| Hikari pending / Tomcat busy peaks | 21 / 55 | 50 / 80 | No Data |
| Mean section lock wait / hold | 1.29 / 2.80 ms | 0.93 / 3.67 ms across ramp and hold | No Data |
| Sampled GC pauses | 200, 0.50 s | 54, 0.466 s in the target hold sample span | No Data |

These historical windows differ in route, load and length. A matched comparison uses one build, one HAProxy backend, platform threads, 16 CPU, 17 GiB, Tomcat 80, HikariCP 24, Redis, the PostgreSQL TLS Sidecar, the same 5,000-student fixture and the unchanged k6 workload and timeouts. Each run includes a 60-second ramp, a 30-second target hold, a 120-second measurement and full SQL reconciliation. The 2,500-VU gate requires a passing same-mode 2,000-VU report with matching artifact fingerprints, cleanup and durable reconciliation. Both modes use the original 95% warm-up pace threshold and HTTP/SQL gates. [Historical 2,000-VU report](../../infrastructure/.local/modal/runs/modal-20261108T190216Z-f6ee77/report.json)

After activating the [Modal environment](../modal-deployment.md), plan the original query path or candidate using `mode=baseline` or `mode=joined`. Complete the 2,000-VU controls before planning each mode's 2,500-VU run.

```bash
mode=baseline
vus=2000
python infrastructure/modal/benchmark.py plan --profile mixed --vus "$vus" \
  --resource-profile a --private-db-sidecar --http-experiment direct \
  --transport encrypted-tunnel --pool-size 24 --prefill-pool --split-database \
  --ramp-seconds 60 --ramp-start-vus 1 --spread-start --repeat 1 \
  --session-storage redis --sidecar-cpu-cores 16 --jvm-threads platform \
  --hardware ha-one --tomcat-threads 80 --section-read-experiment "$mode" --max-usd 2.10
```

The estimates are $1.7180 at 2,000 VUs and $1.8989 at 2,500 VUs per arm. At verification, the workspace had $22.1117 metered before credits and $1.8784 reserved, leaving $1.0099 inside the $25 cap. No paid runs or temporary resources were started for this experiment. **Decision: original path remains active.** The candidate stays opt-in for a future matched run. Queue reduction and 2,500-VU qualification remain unmeasured.
