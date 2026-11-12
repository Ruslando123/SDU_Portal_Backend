# RAM / RabbitMQ controlled mixed load experiment

This experiment tests the opt-in memory decision owner at a requested **2,500 VUs**. It does not change the default synchronous registration architecture. Previous reports and Flyway V1–V7 remain intact.

## Fixed configuration and protocol

- Modal SDK 1.6.3; region `us-east`; one Sandbox, one PostgreSQL Sidecar and one RabbitMQ Sidecar. All three share an aggregate **16 CPU / 17 GiB** limit, verified from container quotas. This is not three separate allocations.
- One Spring Boot instance, Java 21 platform threads, **4 GiB maximum heap**, Tomcat **80**, HikariCP **24**, Redis HTTP sessions. The memory owner reserves one of the 24 JDBC connections for fencing and the authoritative database clock; prefill therefore expects 23 idle connections.
- HAProxy uses verified local backend TLS, connection reuse and an 8,192-connection bound. PostgreSQL and RabbitMQ use private bridge networking, certificate verification and authentication. Only the key-authenticated, allowlisted HTTP benchmark gateway is exposed. Management and database ports remain private.
- PostgreSQL 18.6, `shared_buffers=1GB`, `effective_cache_size=4GB`, `max_connections=60`, `commit_delay=0`, `fsync=on`, `synchronous_commit=on`, `full_page_writes=on`. No durability reduction.
- The established scaled fixture: 5,000 synthetic students, 20 distributed sections, 252 seats per section; 2,500 preauthenticated actors. Mixed workload, minimum 1.25-second iteration pace, original 4-second k6 request timeout, CSRF and unique operation keys.
- One 4 CPU / 4 GiB generator runs a 60-second ramp followed by 30 seconds at the target. A second sequential invocation reuses the prepared sessions for 120 seconds only after the first invocation, projection drain, SQL invariants and complete operation reconciliation pass. No concurrent generators and no automatic workload retries after artifact-transfer failure. A separate explicitly recorded harness-correction attempt may be made within the remaining authorized budget; it never replaces the failed attempt.
- Queue bound remains 10,000 pending decisions. The bounded decision-history configuration is 1,000,000 to accommodate this experiment (the current guard counts both new decisions and cached outcomes). The owner lifetime is 1,100 seconds. These are experiment bounds, not capacity claims.
- The former 10-VU smoke used 4 CPU / 8 GiB and a 512 MiB JVM heap. Its short-window RPS is not comparable with this sustained protocol. The synchronous large-load baseline used the larger resource class; introducing RabbitMQ within the same aggregate allocation still changes resource competition and acceptance semantics.

## Qualification gates

Warmup requires the actual target, a complete 30-second window, zero unexpected errors across ramp/hold/drain, zero dropped or interrupted iterations, p95 < 1 second, p99 <= 2 seconds, at least 57,000 target-window iterations and fully accounted recovery. The existing diagnostic pace exception is **not** accepted by this runner.

Measurement repeats the same checks for 120 seconds and at least 228,000 iterations. SQL invariants must all be zero; every acknowledged operation must have a matching durable outcome and audit; no unexplained active enrollments may remain. Original HTTP failures remain failures after successful recovery. Recovery uses the original key; any necessary follow-up withdrawal is separately labeled post-test recovery, not application recovery or measured throughput.

HTTP acceptance is not sufficient for sustained write capacity. At least 80% of the measurement window must have queue observations, projector throughput must reach at least 98% of confirmed throughput, and sampled backlog growth must be at most 100 decisions. The durable quorum queue must drain completely after each phase. Missing measurements fail the gate rather than becoming zero.

Metrics include publisher-confirm time, global memory-lock wait/hold, SQL projection time, confirmed/projected counters, pending projection, HikariCP, Tomcat, JVM, process resources, k6 resources and PostgreSQL wait observations. Instrumentation adds no business-logic optimization.

## Cost and lifecycle

The preparation-time official Workspace rates were CPU `$0.04730/core-hour`, RAM `$0.008/GiB-hour`; Sandbox CPU `$0.141900/core-hour`, RAM `$0.024/GiB-hour`; egress `$0.04/GiB`. The plan includes a conservative 1.75 regional multiplier, a 1,200-second Sandbox bound, up to two sequential 750-second Function bounds plus startup/idle, `$0.08` build reserve and 4 GiB egress allowance. Total planned worst allocation: **$1.97444444 before credits**, rejected if it exceeds `$2.25` or the confirmed available credit is insufficient. Artifact Volume storage is tiny and short-lived; the remaining cap margin covers this additional storage.

The user previously reported $10 available credit; billing adjustments alone are not treated as proof of wallet balance. Exact App/tag attribution is recorded after cleanup; settlement may lag.

Private generator evidence is checkpointed to an ephemeral evidence Volume with checksums and retried reads. Prepared sessions are temporarily included in the private checkpoint so the Function returns only a small checksum manifest. The operator strips cookies and CSRF tokens before writing local artifacts or UI reports; the temporary evidence Volume is deleted after the run. This avoids restricted Function BlobCreate authorization failures for large RPC results. PostgreSQL and RabbitMQ never use this Volume. All workloads stop in `finally`; Sandbox termination removes its Sidecars; App and evidence Volume cleanup are verified.

```sh
# Preparation/cost only:
python infrastructure/modal/memory_load_benchmark.py --available-credit-usd 9.95
# Explicitly authorized single experiment:
python infrastructure/modal/memory_load_benchmark.py --available-credit-usd 9.95 --execute
# Metered attribution after completion:
python infrastructure/modal/billing.py --date 2026-11-11 --run-id RUN_ID
```

For emergency interruption send SIGTERM to the local runner so its cleanup executes. If the operator process is lost, stop its exact Modal App and terminate its exact Sandbox using the SDK, then verify App state, Sandbox inventory, containers and delete its `sdu-evidence-RUN_ID` Volume. The Sandbox and Function timeouts remain backstops.

## Durability boundary

A single quorum broker on temporary Sidecar storage is **not host-loss-tolerant**. Confirmed messages survive the tested application-process/reply failures only while broker storage survives. Loss of the whole Sandbox can lose RabbitMQ and PostgreSQL together. These experiments use synthetic accounts only; this deployment is not a production durability solution.

References: [Modal Sidecars](https://modal.com/docs/guide/sandbox-sidecars), [Modal pricing](https://modal.com/pricing), [RabbitMQ confirms](https://www.rabbitmq.com/docs/confirms), [implementation and recovery](memory-rabbitmq-experiment.md).

## Measured result — 2026-11-11

**FAILED. 2,500 VUs and the 120-second steady-state gate were not reached.** Two attempts are retained. The second changes only evidence transport/cleanup, not the backend JAR, workload, dataset, CPU/RAM, pools or thresholds. Both execute JAR SHA-256 `313c3b66aa45b517607d5f8293d23209439dcdc2eaf6adb3bcd9b0733f1a1891`.

| Observation | Initial attempt | Evidence-corrected attempt |
| --- | ---: | ---: |
| Run | `modal-20261111T115233Z-e38a6c` | `modal-20261111T120147Z-efc02c` |
| Actual peak VUs | 1,988 | 1,992 |
| k6 ramp runtime before abort | 48.773 s | 48.875 s |
| HTTP requests during ramp/abort | 60,088 | 60,398 |
| Aggregate ramp HTTP RPS | 1,231.99 | 1,235.76 |
| Aggregate ramp HTTP p95 / p99 | 28.92 / 49.20 ms | 61.97 / 88.25 ms |
| HTTP 503 SERVICE_OVERLOADED | 3,126 | 3,240 |
| Additional status-0 results in aborted tail ledger | 6 | 10 |
| Interrupted iterations | 1,989 | 1,993 |
| 30-second target hold / 120-second measurement | Not reached / Not Tested | Not reached / Not Tested |
| Modal metered cost before credits | $0.35998500 | $0.68657420 |

These are **changing-load ramp aggregates**, not sustainable capacity at 1,992 or 2,500 VUs. Successful responses and cheap overload rejections both contribute to HTTP RPS. The k6 safety threshold aborted both ramps after the unexpected outcome rate exceeded its configured bound. The raw ledger additionally captured cancellation-tail status-0 responses which did not all reach k6 counters before interruption; none is silently discarded. No completed target-window latency or throughput is invented.

### Confirmed bottleneck

The single SQL projection worker could not keep up with broker-confirmed acceptance. During the second ramp, HTTP acknowledged **17,305 unique successful mutations / 48.875 s = 354.06 decisions/s**, while PostgreSQL committed **7,319 / 48.875 s = 149.75 decisions/s**. Reconstructing acknowledged-but-not-yet-committed operations from HTTP end times and PostgreSQL commit times gives a peak near **9,999**, consistent with the **10,000 pending-decision guard**. This reconstruction uses different container clocks and is labeled an estimate, not a sampled gauge peak.

The overloaded branch can be identified more precisely from complete counters: all **22,509 mutation attempts**, including recovery, match **22,509 memory-lock acquisition and hold observations**. Thus these requests were not rejected by admission before the lock, and no acquisition timed out. The decision-history cap was far from exhaustion. The remaining `SERVICE_OVERLOADED` branch is the pending-projection guard. The run remained READY; it did not fail publisher confirmation or SQL projection.

Final whole-run timers, including recovery and drain:

| Step | Mean | Recorded maximum |
| --- | ---: | ---: |
| Memory lock wait | 3.145 ms | 43.592 ms |
| Memory lock hold | 0.937 ms | 11.081 ms |
| RabbitMQ publisher confirmation | 0.672 ms | 7.241 ms |
| One SQL projection transaction | 4.895 ms | 50.755 ms |

The final SQL projection ran serially, including its queue receive/ACK work. The 4.895 ms transaction mean alone permits only about 204 transactions/s of continuously busy SQL work; actual end-to-end projection in the ramp was lower. Moving acceptance into memory did not eliminate the work needed to persist decisions.

For successfully acknowledged business operations during the ramp, enrollment was **180.70/s** (p95 **71 ms**, p99 **88 ms**); withdrawal **173.36/s** (p95 **58 ms**, p99 **68 ms**). Recovery is excluded. For 19,237 acknowledged operations across ramp and recovery, PostgreSQL commit followed the HTTP reply by approximately **24.308 s median**, **52.136 s p95**, maximum **53.162 s**. Near-zero cross-clock differences are uncertain; tens of seconds of projection lag are unambiguous.

One private observation during the ramp recorded **11,278 confirmed**, **5,502 projected**, **5,777 pending**, HikariCP active **3**, pending **0**, Tomcat busy **5**. These are observations, not peaks. The separately retained PostgreSQL series has **46 ramp samples**, peak **24 connections**, **3 active**, and **0 observed lock waiters**; sampling does not prove that no short lock wait ever occurred. k6 consumed **61.47 CPU-seconds** and peaked at **1,585.801 MiB process RSS**, within its verified 4 CPU / 4 GiB allocation. Process usage provides headroom evidence, but generator throttling and aggregate cgroup memory peaks were unavailable.

### Durable state and incomplete recovery

The second attempt retained the full HTTP intent/result/retry ledger, every PostgreSQL durable outcome, audit and final enrollment state. After projection drained:

- **19,615 distinct attempted operation keys**, **22,509 HTTP mutation attempts**.
- **19,243 confirmed decisions = 19,243 SQL outcomes = 19,243 audit records**; queue ready/unacknowledged counts both zero.
- **0 mismatches** and **0 unobserved durable operations**.
- 19,237 durable operations had a successful client acknowledgement; **6 committed without an observed acknowledgement**. Another **372 attempted operation keys had no durable commit** after a drained, healthy journal.
- All **8 structural SQL invariants passed**; 21 concurrent invariant checks also passed without missed observations.
- Bounded post-test HTTP recovery resolved **1,111 of 1,489** pending journeys within its 60-second limit. It did not finish every requested cancellation.
- **73 active enrollments remained**. For **68**, enrollment was acknowledged but withdrawal attempts received 503 or had no response. For **5**, enrollment committed despite a lost/aborted response and no withdrawal intent was recorded. All 73 have their complete synthetic request/key/durable-state evidence in `active-enrollment-reconciliation.json`.

These are explained durable enrollments, not a rollback assumption or an overbooking defect. However, the scenario's zero-active-enrollment gate **failed**. No rows were manually deleted to make it pass. Terminating the disposable Sandbox after evidence export removed the synthetic deployment, not the historical report.

### Harness defects and evidence limitations

The first attempt checkpointed its worker result but returned the prepared session list in its RPC result. The large result triggered Modal `BlobCreate`, rejected with HTTP 401 in the restricted Function. Its checkpoint was recovered after cleanup; final PostgreSQL reconciliation for that first attempt was unavailable and remains **No Data**. The corrected attempt returns only a small checksum manifest; the private checkpoint carries prepared sessions until server-side retrieval. Sessions are stripped before local/UI reports. Volume cleanup uses the documented `Volume.objects.delete` API; the first leftover evidence Volume was then removed and verified.

The second attempt's resource sampler was started under a short-lived Sandbox exec rather than its supervising startup process. Its time series was unavailable. Final Micrometer counters, a private point observation, complete PostgreSQL sampling and the operation ledgers remain available; **no Tomcat/HikariCP peak or Spring CPU/RSS timeline is claimed**. Sampling is now attached to the startup supervisor and checked before future loads. This sampling correction has local syntax/test validation only; it was not cloud-retested. No further paid attempts were made to improve the result.

### Cost, cleanup and decision

Combined metered cost: **$1.04655920 before credits**. The amounts are attributed to exact App IDs/tags as of 2026-11-11 and may settle further. They are usage, not a verified remaining wallet balance. Both Apps are stopped; no active Sandbox or container remains; both temporary evidence Volumes were deleted. Existing unrelated model Volumes were untouched.

**Do not promote this experiment to production or claim 2,500 VUs passed.** Keep the synchronous release baseline and retain RAM/RabbitMQ as opt-in research. The next justified experiment is an explicitly designed, bounded improvement to SQL projection throughput/order and recovery, with atomic audit/idempotency and replay tests. Increasing queue capacity alone would only allow more lag; no such increase was made. Host-loss durability remains Not Tested.

Validation: 117 backend tests, 40 Modal runner tests, Go gateway tests, 9 frontend data tests, TypeScript checks and a Chrome UI check. Historical Flyway V1–V7 checksums were unchanged. Performance Lab exposes this mode separately and labels the ramp and missing full measurement explicitly.
