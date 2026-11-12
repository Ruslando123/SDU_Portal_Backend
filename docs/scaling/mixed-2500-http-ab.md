# Mixed 2,500-VU HTTP route A/B on Modal

The [previous loopback TLS run](mixed-2500-loopback-tls.md) reached 2,500 VUs and failed its target hold. HAProxy generated `503 sC--` on the loopback upstream. Other requests reached the backend gateway and returned 200 or 201 after the client deadline. These are separate failure classes. [HAProxy termination states](https://docs.haproxy.org/3.2/configuration.html#8.5)

## Controlled procedure

Route A uses the authenticated outer gateway, HAProxy and the internal TLS gateway. Route B replaces the HAProxy hop with a verified direct TLS connection from the same authenticated outer gateway to the same internal gateway. Both retain a distinct internal capability and a certificate checked against an ephemeral CA and `localhost`. The only externally forwarded port is the authenticated HTTPS ingress. The TLS gateway, Spring Boot and PostgreSQL remain private. The direct client uses a bounded idle connection pool of 256 and the same two-second overall upstream connection budget. Its transport policy is part of the route change.

Both routes use one backend with a PostgreSQL 18.6 Sidecar, a shared 9 CPU / 17 GiB Sandbox, a 4 CPU / 4 GiB generator, a HikariCP pool of 16, 5,000 synthetic students and 5,040 seats. The protocol calls for a full 1,000-VU route A control, a diagnostic ladder per route, then one full 2,500-VU measurement if a route qualifies. The ladder holds 1,500 and 2,000 VUs for 15 seconds each, then holds 2,500 VUs for 30 seconds. The final gate requires all holds to complete within their pacing and latency thresholds with zero unexpected responses. A diagnostic-only result cannot qualify capacity in Performance Lab.

## Instrumentation

HAProxy exposes read-only Runtime API statistics on a local Unix socket. The sampler retains queue, active connection, opening, reuse and error counters. It performs one verified loopback TLS probe per second with independent TCP connect and handshake timers. These probes use fresh connections. The Go TLS listener records accepted and closed connections, ClientHello, verification, resumed sessions and HTTP-active transitions. Process counters include file descriptors, `RLIMIT_NOFILE`, CPU ticks and scheduler wait when the Sandbox exposes them. TCP listen queues, drops, retransmits and errors come from `/proc`. Tomcat connector metrics come from the private Micrometer scrape. Unavailable counters remain null. [HAProxy Runtime API](https://docs.haproxy.org/3.2/management.html#9.3) · [Tomcat connector limits](https://tomcat.apache.org/tomcat-10.1-doc/config/http.html)

## The earlier 522 client timeouts

All 522 client timeout traces from the earlier 2,500-VU hold reached the outer gateway. The gateway recorded request cancellation on 504 and an upstream HTTP 200 or 201 on 18. For eight of those 18, the upstream response header timestamp was later than the client timeout timestamp. These 18 outcomes establish that a client timeout can accompany a successful upstream response. The gateway did not retain the upstream phase at cancellation for the other 504.

Of the 522 timeouts, 343 were reads and 179 were mutation attempts. For the mutation attempts, the durable ledger places the client timeout before the same attempt's commit in 158 cases, attributes the commit to another attempt in five cases and leaves the timestamp order within the clock uncertainty margin in 16 cases. The database outcome was available in each case. The earlier HAProxy/node sample of 500 failed IDs was drawn from the ramp and does not directly classify the 522 timeouts during the hold.

## 1,000-VU control

Route A run `modal-20261108T142710Z-ee9a02` completed 120 seconds and reconciled 66,316 durable operations with zero unexpected HTTP outcomes and zero remaining active enrollments. It missed the HTTP and pacing gate at 1,044.68 RPS, p95 1,015.02 ms and 84,294 paced iterations. The earlier qualified 1,000-VU result remains the published capacity baseline.

HAProxy opened 12 upstream connections and reused 22,824 in the first 20 seconds of measurement. At 60–80 seconds it opened 17,385 and reused zero. Its reported rolling connect average reached 1,040 ms and its queue stayed empty. At 60–80 seconds it used 0.916 of its one configured thread. The independent probes over the full measurement recorded TCP p95 of 0.692 ms and TLS p95 of 3.443 ms, with zero probe errors, zero listen drops and zero listen overflows. Tomcat reached 80 busy threads during sampling, with 422 connections against its configured maximum of 8,192. The control observed connection churn and HAProxy connect delay without a sampled listen backlog. Scheduler wait and cgroup throttling were unavailable.

## Route A diagnostic ladder

Run `modal-20261108T143543Z-1192f3` completed the three diagnostic holds and failed the gate. At 1,500 VUs it had p95 1,685.94 ms and zero unexpected outcomes. At 2,000 VUs p95 was 1,856.07 ms with zero unexpected outcomes. At 2,500 VUs, 33,527 attempts in 30 seconds gave 1,117.57 HTTP RPS, p95/p99 2,907.93/2,968.92 ms and six client timeouts. There were 12 client timeouts across the ladder and drain. HAProxy logged 16 `503 sC--` responses, which were not counted as client-observed HTTP 503s by k6. The ledger matched 42,106 durable outcomes, with zero unexplained active enrollments.

HAProxy Runtime API reported `Maxsock=4122`. In HAProxy 3.2.26, `srv_add_to_idle_list` stops retaining idle connections at the configured 25% high FD threshold. That is 1,031 internal descriptors for this run. The 20% low threshold also restricts adding extra idle connections earlier. During ramp, sampled process FD count rose from 744 at 31.9 seconds to 1,263 at 32.9 seconds and 2,084 at 33.9 seconds. The reuse increments over those one-second samples fell from 1,260 to 738 to zero while new connection increments rose from 188 to 711 to 1,252. The live runtime reported zero idle connections while the active frontend connections alone exceeded the high FD threshold. This matches the idle-pool guard in the exact HAProxy source pinned by the benchmark. [HAProxy idle-pool limits](https://docs.haproxy.org/3.2/configuration.html#tune.pool-high-fd-ratio)

At the 2,500-VU hold, HAProxy opened 32,380 upstream connections and reused zero. It used 0.973 of its one configured worker thread and peaked at 4,106 open process FDs. Its sampled queue was zero, its rolling connect average peaked at 1,919 ms and its rolling response average after connection peaked at 10 ms. The independent loopback probes recorded TCP/TLS p95 of 0.302/2.804 ms with zero listen drops or overflows. Tomcat had up to 16 busy threads out of 80 and 17 connections out of 8,192 during the hold. These data identify a HAProxy reuse and connection-management collapse upstream of Tomcat. They do not measure a kernel accept delay for each failed connection.

## Route B with the same 16-connection pool

Run `modal-20261108T144244Z-4bba26` reached 2,500 VUs and completed the three diagnostic holds. It observed no client HTTP 503s. The 2,500-VU hold had zero unexpected outcomes across 45,598 requests at 1,519.93 RPS, with p95/p99 of 1,990.19/2,047.18 ms and 30,419 paced iterations. The gate also retained 25 client timeouts from the rest of the run. All 25 reached the outer gateway and canceled before that gateway recorded upstream headers. The 1,500- and 2,000-VU p95 results were 1,438.91 and 1,696.67 ms. The latter hold recorded one timeout. The ledger and SQL checks verified all 53,516 durable outcomes and zero active enrollments.

At the 2,500-VU hold the internal listener accepted 129 connections across its sampled window, including fresh telemetry probes. All sampled accepts reached ClientHello and TLS verification. TCP/TLS probe p95 was 0.550/3.331 ms with no listen drops or overflows. Tomcat reached its 80-thread limit while the highest connection sample was 3,031 against 8,192 configured and `accept-count=100`. HikariCP peaked at 16 active and 64 pending, with a 9.722 ms mean checkout acquisition in the hold. This establishes a second contention point in the application request path once traffic can reach it. The direct route addresses the HAProxy failure mode and leaves the application-level SLO unmet. The follow-up changes only the HikariCP maximum and minimum idle from 16 to 24 to test the pool contention hypothesis.

## Direct TLS pool comparison and qualification

Run `modal-20261108T145047Z-3bb938` compared the 24-connection pool on the direct route. The 2,500-VU hold had 48,129 successes and zero unexpected outcomes. The full diagnostic recorded one client timeout just before the 2,000-VU hold. p95 at the 1,500- and 2,000-VU holds was 1,463.80 and 1,624.58 ms. The application path still failed pacing and latency thresholds at each hold.

| 2,500-VU diagnostic hold | A · HAProxy, pool 16 | B · direct TLS, pool 16 | B · direct TLS, pool 24 |
|---|---:|---:|---:|
| Duration | 30 s | 30 s | 30 s |
| HTTP RPS | 1,117.57 | 1,519.93 | 1,604.30 |
| HTTP p95 / p99 | 2,907.93 / 2,968.92 ms | 1,990.19 / 2,047.18 ms | 1,923.24 / 1,973.96 ms |
| Client timeouts in hold / entire diagnostic | 6 / 12 | 0 / 25 | 0 / 1 |
| HAProxy `503 sC--` in entire diagnostic | 16 | — | — |
| Paced iterations in hold | 22,571 | 30,419 | 32,048 |
| Acknowledged enrollments / withdrawals per second | 184.23 / 189.07 | 253.70 / 252.53 | 268.93 / 267.13 |
| Durable outcomes reconciled | 42,106 | 53,516 | 55,552 |
| Active enrollments after reconciliation | 0 | 0 | 0 |

The pool change reduced mean checkout acquisition from 9.722 to 7.996 ms. In all six target-hold samples at pool 24, Tomcat still had 80 of 80 busy threads and HikariCP had 55–56 pending. Mean transaction duration in the direct target holds increased from 23.886 to 30.592 ms. The pool change gave a 5.6% RPS increase and a 67 ms reduction in HTTP p95 in these diagnostic holds. Neither direct configuration passed. The remaining end-to-end delay coincided with sustained occupancy of the application thread and connection pools. The data do not establish which individual scheduling, request-queue or database stage caused every remaining client timeout. HAProxy's `maxconn` was 2,048 and no per-server `maxconn` was configured. There were no observed listen drops. The OS descriptor limit was 1,048,576 and distinct from HAProxy's 4,122 internal `Maxsock`. Cgroup throttling and scheduler-wait counters remained unavailable.

The 120-second 2,500-VU run stayed closed because no route passed the complete diagnostic gate. The confirmed level is the earlier qualified 1,000 VUs at 1,196.13 RPS. All four experiments reconciled their durable outcomes and passed the SQL checks. No 5,000-VU run was started.

## Costs and retained evidence

The control cost $0.40470619, A cost $0.37274800, B with pool 16 cost $0.32838529, and B with pool 24 cost $0.37681074. The total was $1.48265022 before credits and the workspace reported $0 billed. All four Apps stopped and cleanup found zero active Sandboxes or containers. Performance Lab shows the diagnostic-only status, route, ladder evidence and private HTTP samples separately from capacity qualification. Raw `http-samples.jsonl`, `tls-listener-samples.jsonl`, hop traces and reconciliation files are retained with each run.
