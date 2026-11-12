# Ordered SQL projection and resumable recovery

This experiment follows the failed RAM/RabbitMQ ramps `modal-20261111T115233Z-e38a6c` and `modal-20261111T120147Z-efc02c`. It preserves those reports and the synchronous release baseline.

## Measured problem and minimal change

The previous second ramp acknowledged about 354 decisions/s while PostgreSQL committed about 150/s. The 10,000 pending-decision bound rejected new operations. The old projector executed approximately **11 application SQL statements per new event**, with one pool checkout and COMMIT per event. The previously quoted 23/16 round trips described the synchronous registration path, not this projector.

`V8__ordered_memory_journal_projection.sql` adds an optional, SECURITY INVOKER PostgreSQL function. Historical V1–V7 are unchanged. With `PORTAL_WRITE_BEHIND_PROJECTOR_MODE=sql`, the projector executes **two application SQL statements** per event: transaction-local timeout settings, then the projection function. Internal database work is still required; BEGIN/COMMIT and the RabbitMQ receive/ACK remain. The `jdbc` implementation remains available and is the default outside the explicitly configured experiment.

No extra projector threads, JDBC connections, backend instances, CPU/RAM, queues or stale account caches are introduced. One ordered consumer still processes one event per SQL transaction. RabbitMQ publisher confirmation and durable queue settings are unchanged.

The function preserves:

1. Permanent audit identity/payload-hash deduplication before an idempotency claim, including after outcome retention expiry.
2. Existing lock order: idempotency claim → term policy gate → student-term → section.
3. Conditional seat increment/decrement, ownership/state checks and immutable enrollment identity.
4. Seat, enrollment, audit and terminal idempotency outcome in a single transaction.
5. ACK only after the Spring transaction commits. SQL failure rolls back all effects and leaves the event recoverable.
6. No re-evaluation of previously confirmed academic decisions against a later registration window.

The function uses the caller's existing privileges, a fixed `pg_catalog` search path, qualified relations and no dynamic SQL. PUBLIC and observer execution are denied. The normal synchronous Enrollment service is unchanged.

A local same-fixture sequential replay comparison of 120 events observed 511.20 ms with the JDBC path and 191.36 ms with the SQL path (~2.67×). It is a small diagnostic with possible JIT/order effects, not a Modal capacity claim.

## Recovery correction

The previous runner tried to recover while SQL projection was still behind, and its four-worker, 60-second pass stopped before all cancellation journeys were visited. Five enrollments committed without a received response; 68 acknowledged enrollments retained failed/missing withdrawal requests.

The new memory-only orchestration waits for a healthy empty journal and zero in-flight API requests for a quiet interval **before recovery**. A private API activity gauge includes security/session work and excludes management scrapes. Recovery then:

- Prioritizes existing withdrawal intents.
- Replays each original mutation with its original principal, target and Idempotency-Key.
- Uses at most 16 recovery workers, three bounded passes and 90 seconds total.
- Rebuilds pending work from the combined original/recovery ledger, preserving a follow-up withdrawal key if its response was lost.
- Checkpoints the combined ledger at pass boundaries; automatically re-executing a failed Modal invocation is prohibited.
- Drains projection again and reconciles **all** durable operations, audit, active enrollments and SQL invariants before qualification.

This is post-test harness recovery toward the fixture's intended zero-active state. It does not change Student Portal into an automatic cancellation flow. The portal retains its saved user intent/key and explicitly retries that intent. A regression verifies that retrying a lost withdrawal after restart cannot cancel a newer enrollment.

Recovery does not erase original HTTP errors or interrupted iterations. A recovered database cannot turn a failed HTTP gate into Passed. If the bounded recovery does not complete, remaining intents and final SQL state are retained and the run fails.

## Fixed workload and budget

The cloud experiment retains 16 CPU / 17 GiB aggregate Sandbox limits, one PostgreSQL Sidecar, one RabbitMQ Sidecar, Redis sessions, HAProxy, a 4 GiB JVM heap, platform threads, Tomcat 80 and HikariCP 24. PostgreSQL settings, durability, 5,000 synthetic students, 20 sections × 252 seats, 1.25-second mixed pace and HTTP timeouts are unchanged. A 60-second ramp plus a 30-second target warmup must pass before 120 seconds of measurement. Required iteration thresholds and HTTP/SQL/queue gates are not lowered.

The lifetime bound is 1,440 seconds for the Sandbox and 1,350 seconds for the memory owner, allowing separate bounded recovery/evidence phases. At most four sequential Function invocations (warmup, warmup recovery, measurement, measurement recovery) use 4 CPU / 4 GiB. No concurrent generators or autoscaling are allowed. The conservative rate calculation, including every invocation's timeout/startup/idle, regional margin, build and egress reserve, is **$2.45896889 before credits**, with a **$2.50 cap**. This uses a conservative $8.85 budget derived from the user's previously reported $10 minus recorded usage, not an inferred wallet balance from billing credits.

```sh
python infrastructure/modal/memory_load_benchmark.py --available-credit-usd 8.85
python infrastructure/modal/memory_load_benchmark.py --available-credit-usd 8.85 --execute
```

All temporary App/Sandbox/Sidecar/Function resources and the private evidence Volume must be cleaned up. Database and broker remain on ephemeral Sidecar disks. Host-loss durability is still unverified and this remains synthetic-only.

## Verification

Regression coverage includes late audit failure rollback, original-order replay after rejected out-of-order delivery, changed-payload collision, deduplication after outcome expiry, COMMIT before lost ACK, account revocation/CSRF, ownership, no overbooking, lost broker confirm, startup recovery, and lost withdrawal replay after a newer enrollment. Recovery tests inject repeated lost DELETE responses and verify one unchanged key across passes. V8 upgrade checks preserve all V1–V7 checksums and verify function privileges.

Official references: [PostgreSQL PL/pgSQL](https://www.postgresql.org/docs/18/plpgsql-control-structures.html), [RabbitMQ consumers](https://www.rabbitmq.com/docs/consumers), [Modal pricing](https://modal.com/pricing).

## Local verification and startup attempt

- 123 backend tests passed, including V8 migration/security and SQL projector rollback/replay coverage.
- 44 Python runner tests passed; gateway Go tests passed.
- Nine Performance Lab tests, TypeScript checks and a Chrome cloud-report UI test passed.
- JAR SHA-256: `72f4252357034f2a1f93f5d6be1661ef675b7d676f0a506da4a608f0faaf03ae`.

Attempt `modal-20261111T123448Z-3fb253` stopped before load. The startup script still required exactly Flyway V1–V7; the application had correctly applied V8. The startup, smoke and benchmark fixture expectations now require V1–V8. No historical migration was edited. The attempt cost **$0.04166395** in metered usage before credits, and cleanup was verified. Its report remains available.

The actual load retry uses a conservative $8.80 remaining allowance. The $2.45896889 bound is per attempt; including the stopped startup, this task's combined worst-case estimate is $2.50063284. Billing usage is reported separately from the user-reported credit balance.

Measured cloud results are appended after the controlled run. No 5,000-VU test is authorized by this experiment.

### Recovery deadline defect found during the first SQL-projector ramp

`modal-20261111T123935Z-8bf82e` failed its ramp and its recovery evidence collection. The operation-outcome snapshot and original HTTP operation ledger were preserved, but the runner had delayed saving the k6 summary until recovery completed. Consequently full-window RPS/latency and exact achieved VUs are unavailable for this attempt. They must not be inferred from partial evidence.

Before recovery, 19,776 confirmed decisions were projected (10,144 enrollments and 9,632 withdrawals). Recovery committed 214 additional withdrawals, then the operator's 150-second wait expired without a final recovery artifact. The final state contains 19,990 durable outcomes and **298 active enrollments**. Eight structural SQL invariants pass, but complete HTTP/DB reconciliation and the zero-active gate do not. The partial comparison flags 214 correlations without a saved recovery attempt; these are evidence gaps, not proof of inconsistent transaction effects. The original ledger contains 3,996 HTTP 503 results and 10 status-zero aborted results.

The implementation constructed a new `urllib` HTTPS handler before checking whether each queued journey's deadline had expired. HTTPS handler construction creates a trust context and loads CA certificates. `ThreadPoolExecutor.map` then continued constructing clients for expired queued journeys. A network-free diagnostic on Linux Python 3.12, with 2 CPU and 16 worker threads, measured **8.635 s for 64 fresh clients versus 0.084 s for a shared verified context**. This diagnostic does not claim Modal request throughput.

The correction checks the deadline before client construction and shares one immutable default TLS context per recovery invocation, preserving hostname verification, CA validation, HTTP/1.1 ALPN and redirect refusal. The original measured summary, console and metrics are now saved before recovery starts. New tests verify that 2,500 expired queued journeys create no client and that recovery retains verified TLS/redirect behavior. Neither measured HTTP timeouts nor k6 workload settings changed.

This failed attempt cost **$0.63945340** before credits (billing settled upward from the first $0.61061703 reading) and its cleanup was verified. A bounded retry, `modal-20261111T125149Z-d9d538`, tests the same backend JAR and SQL projector with the corrected recovery client. Conservative remaining allowance: $8.15; per-attempt upper estimate unchanged at $2.45896889. Source checksums are saved with the run.

References for the client behavior: [Python urllib.request](https://docs.python.org/3.12/library/urllib.request.html), [Python SSL contexts](https://docs.python.org/3.12/library/ssl.html).

## Final controlled repeat

Run: `modal-20261111T125149Z-d9d538`. **Overall FAILED; SQL/recovery PASSED.** The safety threshold stopped the ramp after 56.836 seconds, before 2,500 VUs or the 30-second target warmup. The 120-second measurement was not started. No 5,000-VU run was attempted.

The following figures describe the ramp and aborted tail, not sustained capacity:

| Metric | Previous JDBC projector (`...120147Z-efc02c`) | SQL projector with corrected recovery (`...125149Z-d9d538`) |
| --- | ---: | ---: |
| Actual peak VUs | 1,992 | 2,322 |
| k6 runtime, seconds | 48.875 | 56.836 |
| HTTP requests | 60,398 | 80,280 |
| HTTP RPS, including failures | 1,235.76 | 1,412.48 |
| HTTP p95 / p99, ms | 61.97 / 88.25 | 371.55 / 659.65 |
| HTTP 503 | 3,240 | 4,120 |
| Status-zero aborted-tail responses | 10 | 11 |
| Interrupted iterations | 1,993 | 2,316 |
| Successful enrollment acknowledgements/s | 180.70 | 204.92 |
| Successful withdrawal acknowledgements/s | 173.36 | 195.23 |
| Acknowledged mutations/s | 354.06 | 400.15 |
| SQL decisions committed/s during ramp | 149.75 | 227.64 |
| Final active enrollments | 73 | 0 |

The configured hardware, fixture, pool/thread limits, workload, timeouts and PostgreSQL durability match. SQL projection, recovery orchestration and working telemetry differ. Abort times/achieved concurrency differ, hosts are not identical, and these are single diagnostic ramps. Thus the increased SQL rate (~52%) and lower per-event processing time are preliminary improvement evidence, not a successful constant-load A/B or a claim that HTTP latency improved. The new ramp reached a higher load and has worse HTTP tail latency.

Successful enrollment-only latency: p95 **345 ms**, p99 **459.54 ms**. Successful withdrawal-only latency: p95 **336 ms**, p99 **514.15 ms**. Success here means a RabbitMQ-confirmed decision; it does not mean synchronous PostgreSQL COMMIT.

### Remaining bottleneck

The pending-projection gauge reached **9,999**, immediately below the unchanged 10,000 admission bound. Across 11 in-ramp samples spanning 51.182 seconds, confirmation ran at **433.55 decisions/s** and projection at **238.19/s**; backlog growth was **9,999**. These sampled rates use a shorter window than the HTTP/commit-timestamp rates in the table.

All **4,120 HTTP 503** have the backend business code `SERVICE_OVERLOADED`: 2,874 enrollments and 1,246 withdrawals. The final lock-wait and lock-hold counts both equal **29,984**, matching every recorded mutation attempt including recovery. Maximum observed RAM lock wait was **45.66 ms**, below the five-second timeout. There were 25,860 fresh decisions, four replays and 4,120 rejections; the history bound is far away. Together with the queue evidence, this identifies the pending-projection guard as the source of these 503s, rather than semaphore admission or a lock timeout.

The 11 status-zero results are interrupted GETs at safety abort (3 `me`, 4 `own_enrollments`, 1 `schedule`, 3 `sections`), kept separately from the 4,120 k6 HTTP failures. No response is classified as a client timeout in this final ledger; four mutation responses are absent and are reconciled through original-key replay. Dropped-iteration telemetry is absent (**No Data**, not zero); the test already fails for interrupted iterations and the incomplete window.

SQL projector timer: **2.904 ms mean during the sampled ramp**, max observed **57.725 ms**. Whole-run mean, including drain and recovery: **2.540 ms** over 25,860 events, versus the old whole-run **4.895 ms**. The latter windows differ because recovery now completes. The timer includes event decoding, connection acquisition and SQL/COMMIT; it excludes RabbitMQ `basicGet`/ACK. The remaining time outside it is not attributed to a component without further measurement.

- HikariCP: **24/24 active**, **55 pending** peak; sampled acquisition mean **3.891 ms**, usage mean **3.477 ms**, zero acquisition timeouts.
- Tomcat: **80/80** peak busy.
- PostgreSQL: 53 samples, **24 connections**, up to **6 active**, **zero sampled lock waiters**; only `Client:ClientRead` appeared among active wait events. Sampling cannot exclude shorter waits. SQL-function section lock hold and WAL-flush breakdown are **No Data**; previous synchronous-path timers cannot substitute for them.
- Java: sampled peak **4.55 CPU cores**, RSS **1,307 MiB**, heap **441 MiB**; 133 GC pauses totaling **0.439 s**. JVM `ActiveProcessorCount=4` is unchanged; the Sandbox's 16 CPU is a shared aggregate budget, not dedicated Java CPU.
- k6: **84.25 CPU-s / 56.836 s = 1.48 mean cores** of 4 allocated, peak RSS **1,812 MiB** of 4,096 MiB. This does not indicate sustained generator CPU/RAM exhaustion. Instantaneous CPU peaks, cgroup throttling and aggregate Sandbox memory peaks are **No Data**.

The PostgreSQL projection pipeline remains slower than RAM acceptance. Increasing the queue bound would postpone rejection and increase lag; it was not done. A next investigation should distinguish per-event RabbitMQ pull/ACK time from projector JDBC acquisition, then evaluate bounded delivery prefetch or a reserved connection within the existing total budget. Neither is claimed as proven or implemented by this run.

### Reconciliation and recovery

After load, the journal drained before recovery began. **1,834 pending journeys were completely resolved in one 18.965-second pass**, with 16 workers, 3,117 HTTP requests and four idempotent replays. The 90-second bound was unchanged. All original/recovery attempts are saved; requests were not converted into successes in the original k6 metrics.

Final evidence:

- **25,860 durable outcomes = 25,860 audit events = 25,860 broker-confirmed/projected decisions.**
- **29,984 mutation attempts** reconciled; zero inconsistent operations or unobserved durable outcomes.
- Four originally missing mutation replies recovered through the original operation keys; all final outcomes acknowledged.
- **Zero active enrollments**, zero allocated seats, empty durable quorum queue and no in-flight API requests at final drain.
- All **eight SQL invariants passed**; 22 live correctness snapshots had no missed samples or violation.
- PostgreSQL `fsync`, `synchronous_commit` and `full_page_writes` remained enabled.

This validates post-test recovery for the observed interrupted journeys, plus regression coverage for process restart/redelivery. It does not prove recovery after loss of the ephemeral broker/database host.

### Cost, cleanup and decision

Modal Billing currently attributes **$0.67508732** before credits to the final repeat. Including startup failure **$0.04166395** and the incomplete-recovery attempt **$0.63945340**, this task's metered total is **$1.35620467** before credits. Billing snapshot: `2026-11-11T13:04:38.742014+00:00`. Usage may settle later; per-run reports retain the exact billing timestamp and resource breakdown. The billing API is not treated as proof of the remaining wallet balance. No resources were left running: Apps stopped, zero active Sandboxes/containers, and each temporary evidence Volume deleted after local export. Unrelated existing Volumes were not touched.

**Keep** the SQL projector as an opt-in experiment and **Keep** the corrected recovery/evidence handling. Retain `jdbc` as the default projector and preserve the synchronous release candidate. **Do not promote the RAM/RabbitMQ architecture as qualified for 2,500 VUs.** Performance Lab shows the failed ramp separately from sustained measurements, observed VUs, actual sampled queue/rates, resolved/remaining journeys, SQL state, resource metrics, billing and cleanup. Host-loss durability remains Not Tested.
