# Architecture decision records

Status: preserved Phase 2/3 decisions with Phase 4 foundation evidence and proposed AD-13, November 6, 2026. Accepted design decisions are not university policy or a performance claim; the implemented/tested subset is recorded in the verification report. AD-01 through AD-09 retain the Phase 1 decision identifiers. Phase 3 physical constraints/locks are now specified in [Database](database.md) and [Database concurrency](database-concurrency.md); selected schema/lock mechanisms are verified in Phase 4, while full academic behavior and performance tuning remain future work.

## AD-01 — Modular monolith

- **Context:** Enrollment has cross-module academic invariants and the initial environment is one local workstation.
- **Decision:** One Kotlin/Spring Boot deployable with public module APIs and one root transaction per academic mutation.
- **Alternatives:** Undifferentiated monolith; separate domain microservices.
- **Consequences:** Transactional orchestration is local; dependencies and ownership must be enforced during implementation.
- **Trade-offs:** Shared deployment and resource contention limit independent scaling; explicit modules add some interface discipline without distributed operations.

## AD-02 — PostgreSQL registration authority

- **Context:** Confirmed membership, capacity, credits and replay outcomes must agree after concurrency and crashes.
- **Decision:** PostgreSQL owns enrollment truth, durable idempotency and transactional audit; use its locks and constraints.
- **Alternatives:** Cache-owned seat inventory; eventually consistent separate enrollment stores; application-only validation.
- **Consequences:** Every confirmation requires an acknowledged database commit; temporary loss of PostgreSQL makes registration unavailable.
- **Trade-offs:** A primary database and hot rows bound write throughput. This is preferable to trading away the required invariants; production availability still needs a separate recovery design.

## AD-03 — Per-student and per-section pessimistic coordination

- **Context:** Section-only protection misses cross-section credit/timetable races; one student may also enroll and withdraw concurrently.
- **Decision:** Shared stable term-policy gate, exclusive student-term resource, then exclusive section resources in identifier order, with the idempotency claim first.
- **Alternatives:** Only lock the section; only optimistic versions; SERIALIZABLE for every operation; process-local mutexes.
- **Consequences:** Compliant same-student mutations serialize and different students share only the section they contend for; all writers require the same protocol.
- **Trade-offs:** Hot rows wait and occupy connections. Short transactions, finite admission and retry handling are essential. Stronger isolation remains an option if Phase 3 reveals an uncovered invariant.

## AD-04 — Conditional seat mutation and database uniqueness

- **Context:** Displayed availability can become stale and repeated intentions can use different keys.
- **Decision:** Check affected-row outcome from a conditional occupancy increment under section protection; commit membership with it and require active domain uniqueness. Withdrawal reverses accounting exactly once.
- **Alternatives:** Read-then-write without protection; Redis seat counters; optimistic capacity version alone; conditional-only section path.
- **Consequences:** Stale reads cannot authorize a seat; duplicate enrollment cannot leave an extra seat increment committed. Phase 3 must choose coherent active/history keys and references.
- **Trade-offs:** Explicit section lock and conditional guard deliberately overlap; correctness/readability is prioritized until evidence justifies a reviewed simplification. Cross-row accounting still requires protocol and reconciliation, not only a counter bound.

## AD-05 — Transactional durable idempotency

- **Context:** Requests can overlap or lose their response after commit, including when routed to another backend.
- **Decision:** Scope/fingerprint claims and terminal minimal responses live in PostgreSQL, in the same transaction as membership/audit. No separately committed processing lease. Retry the same key within a defined recovery horizon.
- **Alternatives:** In-memory result cache; Redis-only deduplication; separate claim transaction and expiry/steal lease; no replay support.
- **Consequences:** A crash before commit aborts the claim; a crash afterward leaves a replayable result. Transient failures are not terminal academic denials.
- **Trade-offs:** Durable response storage and cleanup need limits, retention and privacy review. Replay describes history; after expiry, active uniqueness is not a guarantee against a new enrollment after withdrawal.

## AD-06 — Immutable published academic snapshots

- **Context:** Concurrent changes to credits, meetings, prerequisites or transcript evidence could invalidate a student's accepted schedule.
- **Decision:** Enrollment uses versioned published facts and frozen eligibility; no in-place academic edits while a window is open or to versions referenced by active membership. Administrative close/configuration uses exclusive term-gate protection.
- **Alternatives:** Live edits with immediate reevaluation; eventual conflict repair; hold locks on every academic-data row throughout all registration activity.
- **Consequences:** Draft work and future versions are possible; a correction affecting existing students requires a separate revalidation workflow. Closing a window alone does not make existing memberships safe to reinterpret.
- **Trade-offs:** Registrar flexibility is deliberately limited in the baseline; Q-05/Q-08 must validate that product constraint. Capacity changes are a separate bounded section operation.

## AD-07 — Optional Redis and bounded HikariCP

- **Context:** Repeated catalog reads may benefit from caching, while unbounded DB concurrency can make contention worse.
- **Decision:** Start without Redis. Use a measured bounded HikariCP budget; optional Redis caches immutable catalog metadata only when justified. Cache/limiter failure never bypasses database checks.
- **Alternatives:** Mandatory Redis for counters, locks or sessions; one connection per student; increase the pool whenever waits rise.
- **Consequences:** PostgreSQL remains sufficient for baseline correctness and the selected session design; cache introduction needs staleness/fallback policy and actual read-pressure evidence.
- **Trade-offs:** More DB read/session load without caching; simpler failure behavior initially. Small pools bound concurrency but need admission control and empirical sizing.

## AD-08 — Same-origin browser boundary and synthetic MVP identity

- **Context:** Phase 2 explicitly limits the MVP to synthetic data and test authentication. No real SDU account access is required or authorized.
- **Decision:** Spring Security boundary with predefined synthetic test principals and opaque server-side sessions; select PostgreSQL-backed shared sessions for the design. Cookie-authenticated mutations use CSRF protection. No university login integration in the MVP.
- **Alternatives:** Production SSO immediately; accepting student IDs/roles in arbitrary headers; long-lived browser-stored bearer credentials; sticky in-memory sessions.
- **Consequences:** Test actors exercise student/admin/ownership rules without university credentials; replicas can share sessions. Real identity onboarding is an independent future security task. Session-store work is part of DB sizing.
- **Trade-offs:** Additional relational session data and authentication load, plus strict separation of test login from any future production profile. This refines the formerly unresolved MVP identity choice; Q-01 remains open for real deployment.

## AD-09 — Repository and documentation ownership

- **Context:** Phase 1 inspected the empty local planning workspace and the supplied backend/frontend remotes, which then advertised no refs. Those observations are historical, not a new remote audit.
- **Decision:** Maintain shared design here for this phase; link the application repositories without remotes/submodules, pushes or copies. Preserve all requirement and decision identifiers.
- **Alternatives:** Put all shared docs in backend; create a dedicated contracts/infra repository; combine application repositories.
- **Consequences:** Design is reviewable now; Q-12 must assign an authoritative shared documentation/contract location before implementation distribution.
- **Trade-offs:** Avoid premature synchronization machinery, but ownership/release coordination is not yet resolved. This decision does not publish any content.

## AD-10 — Scale interchangeable backend replicas only after measurement

- **Context:** The 5,000-active-student requirement is an unverified workload hypothesis; a hot section is serialized at PostgreSQL.
- **Decision:** Establish and tune a single-instance baseline first. Add load-balanced instances for demonstrated application CPU/request pressure or restart availability, with shared sessions, aggregate pool/admission limits, and cross-instance correctness verification.
- **Alternatives:** Scale replicas by user count alone; microservices from the start; sticky sessions and process-local enrollment locks.
- **Consequences:** Same-key replay and all invariants stay database-backed. Replica count does not increase capacity of one hot section; the database remains shared.
- **Trade-offs:** Additional operational/session/routing work buys application capacity and controlled draining, not automatic linear throughput. Resource headroom and a consistent benchmark must justify rollout.

## AD-11 — Defer microservices, Kubernetes, brokers and waiting rooms

- **Context:** No measured independent deployment, orchestration or queue requirement has been supplied. Distributed seat allocation would complicate the strict transaction boundary.
- **Decision:** Use a future Docker Compose local environment and synchronous registration. No microservices, Kubernetes or broker. A waiting room is only a future admission option if a measured burst and a product policy justify it.
- **Alternatives:** Split services and add messaging now; queue all seat allocation; introduce a hosted waiting room by default.
- **Consequences:** Fewer runtime dependencies and clearer commit semantics. Service extraction or waiting-room admission would need a new ADR, contracts, failure tests and explicit fairness/UX rules.
- **Trade-offs:** Less independent scaling and no fairness/seat-reservation promise. The initial design favors a bounded, observable write path over unproven infrastructure complexity.

## AD-12 — Separate authoritative schedule reads from enrollment validation

- **Context:** A named Student Schedule module is required, but Enrollment depending on it while Schedule depends on Enrollment would create a cycle. Separate READ COMMITTED queries could also combine different committed states.
- **Decision:** Student Schedule composes public Enrollment/Catalog/Sections read APIs under one short read-only REPEATABLE READ snapshot, or a single equivalent snapshot query behind those APIs. Enrollment validates its own current memberships under student-term protection and never calls Schedule.
- **Alternatives:** Bidirectional services; cache/materialized schedule as the write authority; several unrelated reads with no shared snapshot.
- **Consequences:** No extra schedule ledger or async projection; fresh post-confirmation reads use primary PostgreSQL and see a coherent committed state. Query participants join the read transaction.
- **Trade-offs:** Query composition needs explicit APIs and short transactions; read snapshots do not promise visibility of commits occurring after the snapshot begins. Own schedule caching/read replicas are outside the baseline.

## Revisit conditions

Review AD-03/AD-04 if physical foreign-key/index interactions or measured hotspot time demand a different lock protocol; AD-06 if live academic corrections are mandatory; AD-07/AD-10 if baseline resources identify a specific bottleneck; AD-08 before any real-account integration; AD-09 before distributing implementation. Phase 4 foundation was subsequently authorized and implemented; Phase 5 was subsequently explicitly authorized; they do not authorize Phase 6.

## Phase 3 physical elaborations

Existing AD identifiers and decisions remain intact. AD-03/AD-04 map to the stable term gate, student-term PK, section row, composite FKs and retained episodes with active-only uniqueness. AD-05 uses a shared technical operations table with immutable terminal results, deferred completion guard and old-UUID expiry/reuse. AD-06 uses one immutable offering snapshot per course/term and frozen expanded meetings/eligibility. AD-08 uses the standard two-table Spring Session JDBC layout. AD-12 remains an authoritative coherent snapshot query with no schedule ledger.

No stored student credit counter or aggregate seat-count trigger is selected. These choices and remaining DBF risks are documented in [Database](database.md) and [Data integrity](data-integrity.md), rather than duplicating the schema in ADRs.

## AD-13 — Prepare immutable academic facts before the hot section lock

- **Context:** The Phase 2 baseline obtains section U before academic queries, extending the hot-row hold time. The Tech Lead asked Phase 4 to investigate later acquisition while preserving AD-03/AD-04.
- **Decision:** Adopt the late-section protocol in Phase 5 after runtime/FK/privilege review: claim → gate S → student-term U → read/evaluate stable academic facts → section U → fresh wall-clock and mutable-state revalidation → conditional allocation, membership/audit/result → root commit. Phase 4 supplied primitive tests; Phase 5 implements the production core service.
- **Alternatives:** Keep the original early-section baseline; conditional-update-only section path; release the student lock before waiting. The latter would reintroduce cross-section student races and is rejected.
- **Consequences:** The proposed order of acquired locks is unchanged. Own ACTIVE membership cannot change under student U; published facts cannot change under gate S and the immutable-version lifecycle. Recheck target identity/publication, current capacity, correct window and every time-sensitive eligibility/freshness rule after waiting. Preserve denial precedence by delaying final response selection until that fresh decision.
- **Trade-offs:** More student/gate hold time can precede the section lock; live academic edits or unreviewed writers invalidate the proof. Two-connection tests verify unlocked academic reads, latest capacity, fresh same-student credits/meetings and natural close after waiting. They establish selected database properties, not measured latency improvement or a complete academic implementation. Phase 5 core acceptance tests and the controlled allocation passed; administrative writers/live edits and deployment capacity remain outside this evidence.

See the [candidate protocol](database-concurrency.md#phase-4-late-section-lock-candidate) and [executed verification report](phase4-verification.md). AD-01 through AD-12 remain intact.

Phase 5 adoption evidence: [verification](phase5-verification.md), including the F5-01 V7 lock capability, real Spring proxy/xmin check, two application instances, fresh post-wait time, rollback and complete 2,000-student allocation. No throughput improvement or 5,000-user capacity is asserted.
