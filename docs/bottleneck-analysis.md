# Phase 6 bottleneck analysis

The small observed range reached about 16.8 mixed HTTP requests/s with 10 VUs and about 30 requests/s during the short registration spike. Within that range there was no evidence of sustained Hikari, JVM CPU, storage or generator saturation. The clear observed controls were same-section serialization and the deliberately bounded login verifier; host scheduling/swap limits dominated invalid runs. This analysis does not identify a service capacity limit.

## Evidence and interpretation

| Component | Observed evidence | Interpretation and limit |
| --- | --- | --- |
| Login | Ten simultaneous auth flows produced eight successful logins and two 429s; five flows all passed | The 8-verification admission gate is a real bound. Do not multiply login on every iteration or hide its 429s. Startup burst and warmed student workload require separate measurements. |
| Hot section | Four 100-attempt HTTP runs each ended with 30 ENROLLED/70 SECTION_FULL, zero technical errors. Mean section query duration 5.72–9.20 ms vs student lock query mean 0.42–1.24 ms | Section-row contention is measurable even in this small paced fixture. Query timer also includes SQL/round-trip overhead. A database wait event is needed to identify pure row contention. |
| AD-13 | Average section hold 4.68 ms late vs 7.09 ms early; same lock order, fixture and backend image | About 34% less measured time under the section lock in this limited experiment. End-to-end tails were mixed; no throughput gain established. Keep the late default. |
| Hikari | Normal mean acquire 0.158–0.262 ms, sampled pending 0. Fault test: active/max 2/2, pending 1, acquisition timeout and HTTP 500 | Not the normal-range bottleneck. Deliberate exhaustion proves queue/failure visibility; indiscriminately enlarging the pool can increase database contention. |
| Cold path | First 10-user smoke p95 519 ms, while warmed steady mixed p95 was 19–30 ms across exploratory/controlled runs | First-use JVM/framework/connection/data paths and burst mix are confounded. Keep startup and steady measurements separate. The samples do not attribute all cold latency to one component. |
| JVM/generator | Controlled normal backend sampled peak 33–48% of one core, native k6 RSS about 53–61 MiB; raw CPU/heap/GC snapshots preserved | No sustained saturation was observed; periodic samples can miss brief bursts. Shared 8-GiB host with 5.5–7.6 GiB swap and long suspension gaps cannot validate a large ladder. |
| PostgreSQL I/O | Warm EXPLAIN probes used only shared buffer hits; run snapshots include reads, I/O time and WAL-related statements with durability on | No disk bottleneck demonstrated at this scale. Do not extrapolate to large durable-write or long history workloads. |

## SQL and query plans

[Raw plan summary](phase6-assets/query-plans-summary.json) records actual PostgreSQL 18 execution. Plan probes use fixture literals and rollback lock probes, so they illustrate the selected access paths; they are not a promise that every JDBC generic/custom plan or future large history uses the same choice.

| Probe | Plan | Rows | Execution ms | Shared hits / reads |
| --- | --- | ---: | ---: | ---: |
| Student-term lock query | LockRows → Index Scan, uq_student_term_policy | 1 | 0.752 | 5 / 0 |
| Section lock query | LockRows → Index Scan, uq_section_identity_offering_term | 1 | 0.624 | 4 / 0 |
| Own history | Limit → Sort → Seq Scan on the small retained ledger | 1 | 0.120 | 7 / 0 |
| Published offering | Index Scan, uq_offering_term_course | 1 | 0.423 | 3 / 0 |
| Published sections | Limit → Sort → Index Scan, uq_section_term_offering_code | 3 | 0.204 | 8 / 0 |
| Two-section meeting batch | Sort → Bitmap Heap/Index Scan, uq_meeting_section_interval | 4 | 0.612 | 12 / 0 |
| Missing idempotency scope | LockRows → Index Scan, uq_idempotency_scope | 0 | 0.402 | 4 / 0 |

In controlled-normal10-r2, pg_stat_statements counted 503 session joins (53.30 ms cumulative, 0.106 ms mean), 413 session updates (44.17 ms, 0.107 ms mean), 90 idempotency claims (47.16 ms, 0.524 ms mean), 80 terminal outcome writes (40.08 ms, 0.501 ms mean) and 80 student locks (15.90 ms, 0.199 ms mean). The reset plus warmup is included in this SQL snapshot, not in target HTTP timing. Account validation is another regular per-request lookup; session persistence is a real database cost despite avoiding login on every iteration. The runtime-role filter separates these from diagnostic collector queries. The legacy exporter itself also issues diagnostic relation/statistics reads; do not mistake observer SQL for business SQL.

The published section read batches meeting occurrences for the selected section IDs. Enrollment batches credits and meetings for the student's current active set, and reads pinned evidence/prerequisites through bounded calls. No per-offering or per-meeting query loop is present in these measured paths. This is source/statement-family evidence, not a claim about unrelated future catalog/search/admin endpoints. The small own-history sequential scan is not a reason to add a new index without a larger history plan comparison.

## Request-stage evidence

The before/after snapshots provide durations for account validation, Hikari acquisition, idempotency, shared gate, student lock, academic preparation, section lock/hold, commit and root attempt. PostgreSQL statement time and I/O give a separate server perspective. HTTP client duration includes routing, session resolution/persistence, queueing, encoding and transport; timer groups overlap. Admission is a bounded semaphore, not a measured distributed queue. Root attempt includes acquisition and the transaction interceptor. Section hold approximates time after the locking statement returns through Spring completion. Do not sum the timers to reconstruct a request or interpret a query-duration histogram as pure physical lock wait.

## Reliability finding and next experiments

1. **Session-store fault handling.** Phase 6 observed HTTP 500 on PostgreSQL shutdown and forced pool exhaustion. Phase 7A handles confirmed temporary failures before HTTP commitment and retains same-key recovery after an ambiguous write. The historical 500 measurements remain in the Phase 6 artifacts. [Phase 7A reliability verification](phase7a-reliability-verification.md).
2. **Measure session and login cost separately.** Retain adaptive password verification and bounded login. Preauthenticate outside steady tests, then run a dedicated measured login rate sweep on the other host. Evaluate session DB work and auth version checks before proposing any storage or caching change.
3. **Retain AD-13 and verify at meaningful contention.** Use the 2,000-HTTP-attempt profile with enough prepared sessions, compare hot vs spread-capacity workload and verify exact allocations. Evaluate the same Java/DB/pool limits first; any shorter transaction or SQL query consolidation needs before/after evidence and unchanged invariants.
4. **Find resource limits on a separate generator/host.** Increase offered rate and VUs stepwise, record generator CPU/RSS/drops, backend CPU/GC, Hikari pending and PostgreSQL locks/I/O. Compare pool sizes only with the same workload and durable database configuration. A higher VU setting without achieved traffic and invariants is not success.
5. **Measure history and durability costs before changing indexes.** Repeat plans with a larger retained episode/outcome fixture, representative cache state and commit load. Keep credits, schedule and active uniqueness constraints authoritative. Do not disable fsync or synchronous commit.

The 16,800-occurrence fixture and the Windows stage plan are ready for the next measurement series. Any changed resource limit or dataset belongs to a separately labeled run. Establish a Windows baseline before comparing an optimization on that machine. [Phase 6 measured results](phase6-performance-report.md), [Windows benchmark setup](windows-benchmark-setup.md).
