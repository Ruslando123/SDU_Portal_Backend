# Scaling, admission, and failure behavior

Status: Phase 2 design reconciled with the Phase 3 [database lock/integrity specification](database-concurrency.md). Phase 4 has a local PostgreSQL/backend foundation with bounded development pool defaults, not a shared deployment, tuned pool size, load result or established 5,000-student capacity. [Performance](performance.md) remains the source of numerical hypotheses and workload definitions.

## Evolution from one backend instance

| Stage | Shape and engineering trigger | Evidence required before progressing |
| --- | --- | --- |
| Baseline | One Next.js frontend, one Spring Boot modular monolith, one PostgreSQL database, bounded HikariCP, same-origin routing; planned Docker Compose local environment | Correctness suite and a measured resource/latency baseline on a named environment |
| Tune the measured bottleneck | Review query plans, indexes, transaction duration, payloads, JVM/GC, pool waits and generator capacity before adding infrastructure | A specific bottleneck and a change that improves it without violating invariants |
| Add backend replicas | Reverse proxy/load balancer sends requests to interchangeable instances when application CPU/request capacity or restart availability justifies it | Database has headroom; cross-instance races/replay pass; session continuity and total connection budget are verified |
| Add optional caching/admission | Versioned catalog caching or stronger shared admission only when measurements identify repeat reads or excessive offered load | Hit/fallback behavior, overload recovery, bounded queues and backend-independent correctness |

More backend instances do not increase the write concurrency of one section row. They can worsen database contention and connection pressure. PostgreSQL remains the sole authority for confirmed registrations, even across replicas. No queue, broker, read replica, shard, Kubernetes deployment or microservice is part of this baseline.

## Routing and instance lifecycle

Use one public origin: UI routes go to Next.js, `/api` to Spring Boot. The proxy terminates TLS in a shared deployment, bounds request/body/connection work, forwards correlation IDs safely and trusts forwarding headers only from its configured upstream. No vendor/product choice is necessary now.

The load balancer checks readiness and drains an instance before shutdown. Stop admitting new mutations, allow bounded in-flight transactions to finish, then close resources; an interrupted transaction follows normal rollback/replay semantics. Liveness represents process health, not a reason to restart every process during a shared database outage. Readiness reports inability to serve required API work without repeatedly running expensive checks.

No sticky-session correctness requirement: the proposed opaque session store is PostgreSQL-backed, so any healthy backend can authenticate the same session. The browser keeps the mutation key across reconnects and routing changes. Session persistence introduces additional database work and must be included in the baseline; it is not free scalability.

## HikariCP and bounded concurrency

Pool size is a measured database concurrency limit, not a function equal to student count. The project's hypothesis is that short bounded database work and controlled admission will outperform unbounded concurrent waiting; no numerical pool size is prescribed. HikariCP's own [Pool Sizing guidance](https://github.com/brettwooldridge/HikariCP/wiki/About-Pool-Sizing) warns that increasing connections can harm performance; its heuristics are not a benchmark for this hardware.

Use the budget relationship:

`sum(maximum connections of every application/session pool) + monitoring/migration/admin reserve <= approved PostgreSQL connection budget`

For equal instances, `instance_count × per_instance_pool_limit` is only the application part. Include any separate session datasource/pool. Prefer one coherent bounded datasource arrangement initially; do not introduce hidden nested connection acquisition on the enrollment path. A pool-size increase requires database CPU/I/O/lock-wait evidence, not merely a high pending count.

Bound admitted requests before connection acquisition as well as Hikari's connection wait. Bound proxy queues, backend executor/admission queues, lock wait, statement time, root transaction time, total retries and HTTP time. A coherent order reserves enough time for rollback/response inside the outer request deadline. Exact budgets are TODOs to measure; timeouts must not be set to the full outer deadline independently at every layer.

Hot-section requests can occupy pool connections while waiting for locks. Finite admission and wait deadlines limit that cost; preserve headroom for reads and admin close. Pool saturation yields `503 TEMPORARILY_UNAVAILABLE` with retry guidance, not a synthetic seat denial. Track acquisition wait, pending work, utilization and lock wait together.

## PostgreSQL limits and mitigations

| Potential bottleneck | Evidence to collect | First justified response |
| --- | --- | --- |
| Hot-section serialization | Lock wait and successful allocation rate for the hot fixture | Shorten protected work, cap waiting requests, separately report burst latency; more API replicas do not remove the row boundary |
| Same-student request flood | Repeated-key/duplicate rates and student-term waits | Per-principal admission, replay before domain locks; preserve cross-instance database protection |
| Excess connections | Aggregate pools, DB active/idle/waiting sessions and CPU/I/O | Reduce/tune pools and admission; retain administrative reserve |
| Slow eligibility/schedule/catalog queries | Query plans, row counts, read/write I/O and query duration | Phase 3 access-path indexes, bounded queries, immutable snapshots; avoid extra round trips under locks |
| Commit/WAL/storage pressure | Commit latency, transaction rate, WAL/disk latency | Measure storage and transaction work; keep required durability and transactional audit rather than weakening confirmation semantics |
| Excess catalog/session traffic | Endpoint mix, session-write frequency and DB workload | Tune bounded polling/session persistence; optional immutable catalog cache if it addresses measured repetition |
| Database outage / single-primary limit | Availability, restart/restore and resource evidence | Document recovery and restore targets; production HA design is a separate justified task, not an assumed Docker Compose property |

## Redis caching

Redis remains optional and absent from the default dependency set. Candidate: read-through caching of versioned published catalog metadata with finite expiry, bounded fill concurrency and publication invalidation. A seat display is a separately timestamped hint, not part of an indefinitely cached metadata object. The browser's own schedule remains an authoritative PostgreSQL read, particularly after a mutation.

If Redis fails, bypass it through a concurrency-limited database fallback; coalesce/bound cache fills and shed optional reads if the database lacks headroom. Do not redirect an unlimited cache-miss flood to PostgreSQL. No cache seat counter, cached credit total or distributed lock authorizes registration. A stale catalog can be explicitly labeled or temporarily unavailable; an unverified confirmation can never be substituted for a database commit.

## Rate limiting and a possible virtual waiting room

Initial admission combines coarse proxy connection/request limits and backend per-principal limits on login and mutations. Enrollment uniqueness/idempotency remains necessary when every limiter is bypassed. Distinguish abusive repeated clicks from shared campus/NAT traffic; IP-only policy cannot identify students reliably.

For one instance, local request counters can bound its work. Before replica rollout, do not mistake independent counters for a cluster-wide limit. Options are a shared edge enforcement point or, if exact principal-wide quotas become necessary, an explicitly justified shared limiter. Redis can implement that optional admission feature, but it is still not enrollment truth; failure must fall back to conservative local caps rather than unlimited admission. Per-principal counting should not add a contended transaction to the registration ledger.

Return `429 RATE_LIMITED` for configured requester quotas and `503 TEMPORARILY_UNAVAILABLE` for resource overload, with bounded retry guidance. Both count as unsuccessful service under the target workload in PERF-04. Randomized client backoff reduces synchronized retries; the same idempotency key is retained. The proxy must not silently replay POST/DELETE to another backend after an uncertain outcome without the preserved key and reviewed retry policy.

A **virtual waiting room is deferred**. Consider it only if measured opening bursts exceed bounded admission and the product accepts delayed entry. Its design would require tamper-resistant admission tokens, expiry, refresh/reconnect behavior, capacity, overload metrics and an explicit ordering/fairness policy. It queues permission to attempt registration, not a seat reservation or guaranteed enrollment. It adds no basis for a FIFO seat claim under BR-12; choosing a queue product is premature.

## Graceful degradation

- Keep transactional confirmation, own-schedule correctness, authorization and ownership checks intact.
- Reduce availability polling and expensive optional filters before allowing unbounded read pressure; show observation time or a typed unavailable result.
- Prefer explicit bounded rejection to a request that waits indefinitely. Do not downgrade an unknown commit outcome to `SECTION_FULL` or success.
- Serve static Next.js assets and clearly labeled cached catalog metadata where possible during an API outage. Fresh schedule, enrollment, withdrawal and session operations requiring PostgreSQL become unavailable until recovery.
- A monitoring outage need not reject valid academic operations; business audit persistence remains part of commit. An optional Redis outage must not break correctness or silently remove admission limits.

## Failure scenarios and later verification

All verification below is a future plan, not an executed experiment.

| ID | Scenario | Expected behavior | Protective mechanism | Verification |
| --- | --- | --- | --- | --- |
| FS-01 | PostgreSQL temporarily unavailable | Fresh protected reads/mutations and DB sessions fail safely with `503`; a lost commit acknowledgement stays ambiguous until replay can resolve it | Bounded acquisition/query/reconnect waits, no offline seat acceptance, durable result with domain commit | Cut connectivity before begin, mid-write and around commit; restore and replay keys; inspect BR-01 through BR-09 / CT-05, CT-06, CT-10 |
| FS-02 | Connection pool exhausted | Finite wait or early admission rejection, then `503`; a request never borrows indefinitely or allocates an extra unbounded connection | Admission/executor caps, Hikari wait budget, administrative connection headroom | Hold connections deliberately, verify latency bounds/recovery and no leaked transactions; CT-10 |
| FS-03 | Duplicate enrollment request with different keys | At most one active membership; subsequent completed request returns `ALREADY_ENROLLED` without consuming another seat | Student-term protection, duplicate check, database uniqueness | Race different keys on one and multiple backend instances, including two sections of one offering; CT-02 |
| FS-04 | Student submits twice with the same key | One execution; wait/replay or transient `REQUEST_IN_PROGRESS`, then original terminal result | Unique durable scope claim before domain locks, canonical fingerprint | Double-click and route duplicates to different replicas; mismatch payload; CT-02, CT-06 |
| FS-05 | Transaction deadlock | Victim rolls back entirely; bounded fresh-transaction retry with the same key, otherwise visible retryable failure | Global order, no lock upgrades, short transactions, abort handling and retry deadline | Inject a lock cycle using a controlled test writer, assert zero partial counters/memberships and bounded retries; CT-10 |
| FS-06 | Backend crashes during registration | Before commit: rollback once connection death/timeout is detected. After commit: preserve enrollment and result | One transaction, DB connection cleanup, durable idempotency | Terminate at claim, after seat change, before commit and after commit; reconnect to another instance and replay; CT-05, CT-06 |
| FS-07 | Student loses internet connection | UI shows unresolved intention and retries same key on reconnect; no automatic compensating withdrawal | Client-retained key, authoritative reads and replay, no success inferred from transport | Drop response while server commits; reload/reconnect inside retention; also test outside retention; CT-06 |
| FS-08 | Sudden traffic spike | Bounded queues and controlled `429`/`503`; record overload instead of claiming the target passed | Edge/backend admission, pool/lock deadlines, randomized backoff | Separate opening burst and arrival-rate stress; verify offered/achieved load, rejection and recovery without queue growth |
| FS-09 | Course/section becomes full during request | A protected final decision observes full capacity and denies `SECTION_FULL`; a stale UI is not an allocation | Section protection plus conditional counter mutation and rollback | Race eligible students for final seat(s), inspect ledger/counter and successful allocation count; CT-01 |
| FS-10 | Redis unavailable | If cache enabled, bounded fallback/degraded optional catalog reads; enrollment remains authoritative | Optional cache, finite fallback concurrency and conservative local admission if shared limiter unavailable | Disable Redis during read traffic and hot writes; verify DB load bounds and invariants; CT-10 |
| FS-11 | Enrollment and withdrawal race | Seat released once; subsequent allocator sees either committed release or prior full state, never partial accounting | Same student/section order and transactional state transition | Race withdraw, replay, and another student's enroll; inspect active/historical totals; CT-07 |
| FS-12 | Admin closes registration while requests wait | Already protected transactions resolve; after close commits later validation decisions see closed state | Shared versus exclusive term gate, fresh decision time, finite waiting | Pause requests at each lock level, close, then resume; CT-08 |

## Observability and progression gate

Correlate HTTP outcomes with root transaction attempts, replay/denial/technical classification, lock wait, deadlock victim/retry counts, pool acquisition, queue rejection and JVM/DB resources. Use bounded labels, safe logs and transactional audit as specified in SEC-06. Protect Prometheus scrape and Grafana access. A database exporter is justified only for required diagnostics that application metrics cannot expose; it is not a business dependency.

Before replica experiments: finish the physical transaction/constraint review, pass single-instance correctness tests, settle PostgreSQL-backed sessions, confirm aggregate connection/admission limits and deployment drain behavior, then repeat CT-01 through CT-10 across replicas. Performance and failure evidence is environment-specific. The i5-12400F / Windows 11 workstation from the brief is the intended future local test host, not a proven capacity profile.
