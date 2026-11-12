# Requirements specification

Status: Phase 1 requirements baseline reconciled with Phases 2–5, November 6, 2026. Existing FR/NFR/BR/CT/Q identifiers are preserved. Phase 5 implements synthetic authentication and core enrollment/withdrawal; see [executed verification](phase5-verification.md). Remaining administrative/catalog breadth and performance targets are not claimed complete.

## Scope and evidence

The project brief supplies the stack, the intended local Windows test host, and a target of 5,000 active students. They are inputs, not measurements. The existing local Git repository was empty apart from Git metadata. Both user-supplied remote repositories advertised no refs during inspection; no existing application behavior was verified. This document does not describe or audit SDU's current production system.

In scope: student authentication, browsing courses and sections, enrollment, withdrawal, schedule views, academic validation, administration, and registration windows. The original Phase 1 implementation restrictions were superseded for Phase 4 foundation, SQL migrations, dependency installation, local PostgreSQL and integration tests. Phase 5 subsequently authorized synthetic authentication, core Enrollment and a controlled 2,000-student correctness fixture. Frontend, broad administrative APIs, large HTTP load tests, paid services, OpenAI API usage, microservices and Kubernetes remain outside the current phase. Waitlists, reservations, swaps, payments, notifications, and automatic recommendations are outside the initial proposed product scope unless requested later.

Priority: **P0** = mandatory for a correct, usable pilot; **P1** = required engineering quality for a maintainable pilot. Numerical performance targets are unverified hypotheses as defined in [Performance](performance.md). Academic policies labeled provisional require stakeholder confirmation.

## Vocabulary

| Term | Meaning |
| --- | --- |
| Course | Academic subject definition, including credit value and prerequisite policy |
| Course offering | A course published for a particular academic term |
| Section | Enrollable group within an offering, with capacity and meeting occurrences |
| Active enrollment | A committed enrollment currently occupying a seat and counting toward the student's credits |
| Student-term state | Stable coordination record for one student's registration within a term |
| Registration window | Authorized interval and eligible cohort for enrollment and/or withdrawal |
| Confirmed | Committed to PostgreSQL; a received request or UI seat display is not confirmation |
| Policy snapshot | Published, versioned academic rules and schedule data used for a window |

## Functional requirements

| ID | Requirement and description | Priority | Acceptance criterion |
| --- | --- | --- | --- |
| FR-01 | **Student authentication.** Establish a verifiable student identity and role. The MVP uses predefined synthetic test accounts; real university identity integration remains future scope. | P0 | A valid student identity can access its own protected data; invalid, expired, revoked, or missing credentials are rejected. A student cannot impersonate another student by changing a URL or body field. See NFR-05 and SEC-01. |
| FR-02 | **Course catalog.** Show published courses for a selected term with code, title, credits, and prerequisites; support bounded pagination and search/filtering. | P0 | Seeded published courses appear in the correct term and filters; unpublished courses are hidden from students; an empty query result is valid; page size is server bounded. |
| FR-03 | **Course sections.** List sections with offering, capacity, meeting times, and enrollment eligibility information. | P0 | Each listed section belongs to the requested offering/term and exposes every required meeting. Missing timetable data blocks publication/enrollment under the provisional policy. |
| FR-04 | **Course enrollment.** Atomically enroll the authenticated student in a section after all validations. | P0 | An eligible request yields one committed active enrollment. A denied or failed request causes no seat or credit change. CT-01 through CT-06 pass, including concurrent requests. |
| FR-05 | **Course withdrawal.** Withdraw the student's own active enrollment within its allowed window, preserving history. | P0 | A permitted withdrawal releases exactly one seat and its credits atomically. Repetition has no additional effect. Another student's enrollment cannot be withdrawn. CT-07 passes. |
| FR-06 | **Student schedule.** Show the student's committed enrollments, all meeting occurrences, and total active credits for a term. | P0 | After a confirmed mutation, a fresh own-schedule read reflects it; no withdrawn enrollment counts toward active credits or clashes. The response identifies the term and timezone. |
| FR-07 | **Available seats.** Expose remaining capacity without implying a reservation. | P0 | For an authoritative snapshot, available seats equal capacity minus active occupancy and are nonnegative. Responses expose observation time/cache status where applicable. A stale display never authorizes enrollment; CT-01 has no over-allocation. |
| FR-08 | **Credit limit validation.** Enforce the effective student-term credit limit server side. | P0 | A total exactly at the configured limit is allowed; a total above it is rejected. Simultaneous requests for different sections cannot jointly exceed the limit. CT-03 passes. No numeric academic limit is invented. |
| FR-09 | **Schedule conflict validation.** Reject overlapping required meetings within the student's active term schedule. | P0 | Conflicting date/time occurrences are rejected, including concurrent enrollment in different sections. Adjacent meetings are allowed under the provisional half-open interval policy. CT-04 covers every meeting and recurring-date overlap. |
| FR-10 | **Prerequisite validation.** Evaluate the published prerequisite rules against authoritative academic eligibility data. | P0 | Required completed courses and configured minimum grades are checked. A satisfied student is accepted; an unsatisfied student is denied without mutation. Missing/stale required records cause an explicit unavailable/eligibility error rather than an assumption of success. |
| FR-11 | **Admin course management.** Authorized administrators manage draft courses, offerings, sections, capacities, meetings, and prerequisites, then publish valid data. | P0 | A student receives no admin access. Invalid capacity and incomplete/conflicting publication data are rejected. Admin mutations are audited. Published academic facts are frozen during an open window under the baseline policy; capacity cannot be reduced below occupancy. CT-09 passes. |
| FR-12 | **Registration period management.** Administrators configure enrollment/withdrawal windows by term and eligible cohort and can close them. | P0 | Server time and cohort eligibility determine authorization; opening is inclusive and closing exclusive. Before-open/at-close requests are denied. A committed administrative close prevents later validation decisions. CT-08 covers locks, boundaries, and in-flight transactions. |

## Non-functional requirements

| ID | Requirement and description | Priority | Acceptance criterion |
| --- | --- | --- | --- |
| NFR-01 | **Performance.** Measure registration responsiveness under an explicit workload rather than infer capacity from user count. | P0 | A future valid PERF-01 benchmark reaches 5,000 active virtual students, enrollment p95 ≤ 1 s and p99 ≤ 2 s, and unexpected failures < 0.5%. All are unverified hypotheses, with cohort/error definitions in Performance. |
| NFR-02 | **Scalability.** Bound application queues, database connections, response sizes, and request work; support later replica evaluation without process-local correctness locks. | P1 | A future stress test records queue depth, HikariCP wait, CPU/memory, throughput, and overload behavior. No unbounded queue or connection growth occurs. If replicas are tested, cross-instance concurrent writes preserve BR-01 through BR-08. No linear scaling claim is made. |
| NFR-03 | **Reliability.** A crash, connection loss, retry, or dependency outage must not create a partially applied mutation. | P0 | Failure injection around commit produces either a complete mutation or none. An ambiguous client outcome can be resolved using the same idempotency key and authoritative reads. Restart preserves committed data; restore procedure is exercised before a production pilot. Availability SLO, RPO, and RTO remain TODO. |
| NFR-04 | **Data consistency.** PostgreSQL is authoritative for seats, active enrollments, academic eligibility versions, and registration decisions. | P0 | The future concurrency suite and post-test database checks find zero capacity/duplicate/credit/conflict violations and no occupancy drift. Zero observed violations is an unverified benchmark target; the underlying invariants are mandatory. |
| NFR-05 | **Security.** Apply authentication, server-side role/ownership checks, safe input handling, protected sessions, least privilege, and audit. | P0 | Positive and negative authorization cases cover student and admin routes. Secrets and session tokens are absent from Git/logs. Session expiry/logout, CSRF protection for cookie mutations, and management endpoint restrictions pass SEC-01 through SEC-06. |
| NFR-06 | **Observability.** Make request outcomes, latency, saturation, contention, and data integrity diagnosable. | P0 | In a future environment, an injected failed enrollment has a correlated structured log; Prometheus exposes bounded-label latency/outcome and database-pool metrics; Grafana distinguishes business denials from system failures. Tokens and personal academic data are absent from metric labels. |
| NFR-07 | **Maintainability.** Keep a modular monolith with documented interfaces and reproducible builds and migrations. | P1 | Module ownership matches Architecture; domain writes use owning-module APIs rather than foreign repository access. Build/migration instructions, reviewable contracts, and meaningful PostgreSQL integration/concurrency tests exist before implementation is considered ready. Version choices remain TODO. |

## Business rules and proposed concurrency solution

These are proposed rules for the alternative system, not established SDU policies. Requirements BR-01 through BR-08 define safety; operational deadlines and policies need stakeholder review.

| ID | Rule | Conceptual enforcement |
| --- | --- | --- |
| BR-01 | Active section occupancy never exceeds capacity and never becomes negative. | Conditional seat mutation inside the same transaction as the enrollment; nonnegative/bounded counters and reconciliation checks. |
| BR-02 | A student has at most one active enrollment for a section. Provisionally, a student also has at most one active section of a course offering in a term. | Database uniqueness for active records plus application-level domain errors. Linked lecture/lab groups require a revised model before implementation. |
| BR-03 | A student's active credit total cannot exceed the effective student-term limit. | Serialize mutations on the same stable student-term record and validate authoritative totals under that protection. |
| BR-04 | Required meeting occurrences cannot overlap within the student's active schedule. | Use the same student-term coordination boundary as BR-03, validate every meeting against a consistent published timetable. |
| BR-05 | The published prerequisite rule and student eligibility snapshot must be satisfied. | Resolve a local, versioned academic record; no external identity/transcript calls inside a write transaction. Missing eligibility does not silently pass. |
| BR-06 | An enrollment or withdrawal is all-or-nothing. | ACID transaction includes enrollment state, seat/credit effects, audit event, and stored idempotent result; reply with confirmation only after commit. |
| BR-07 | Repeating a mutation with the same scoped key and payload has the same effect and returns the stored terminal outcome during retention. | Durable PostgreSQL idempotency record, unique key scope, request fingerprint, and commit coupled to the mutation. |
| BR-08 | Authorization, term/cohort eligibility, and the appropriate registration window are checked server side. | Protected policy decision inside the transaction; authenticated identity supplies the student, not user-controlled IDs. |
| BR-09 | Withdrawal releases a seat/credits once and preserves history; re-enrollment requires fresh eligibility validation. | State transition under the same lock order; the old enrollment and its idempotency keys are not reused for a new enrollment intention. |
| BR-10 | Enrollment sees stable academic facts. | Freeze published credits, prerequisites, timetable, and eligibility snapshots during an open window and keep versions referenced by active enrollment immutable afterward. Corrections require a separate reviewed revalidation workflow; closing alone is insufficient. |
| BR-11 | Window decision time is sampled from database time after required locks are held, immediately before mutation validation. | Use interval `[opens_at, closes_at)`; a decision made before closing may commit afterward. A close waits for already protected transactions. No promise to cancel committed or already validated work. |
| BR-12 | Allocation promises correctness, not FIFO ordering or fairness. | No seat reservation or waitlist is implied by reading availability or arriving first. A future fairness requirement would need a separate design and benchmark. |

### Why seat locking alone is insufficient

Two requests for different sections do not contend on the same section row. If both read the same old student credit total or timetable, both can pass independently and jointly violate BR-03 or BR-04. The proposed student-term lock covers enrollment and withdrawal across sections, not merely duplicate requests. A stable student-term coordination row must exist before registration opens; locking an absent enrollment row does not provide this boundary.

### Proposed transaction boundary

Phase 2 selects one READ COMMITTED root transaction with a durable idempotency claim, shared stable term-policy gate, exclusive student-term coordination and ordered exclusive section protection. Academic checks use current student membership and immutable published facts; a conditional allocation guard, membership, any maintained totals, audit and terminal result commit together. No confirmation precedes commit.

The canonical detailed algorithm, comparison of locking alternatives, writer inventory, withdrawal/admin interactions and crash/retry sequences are in [Enrollment design](enrollment-design.md). The term-policy gate refines the original window lock without changing BR-08/BR-11; Phase 3 maps it to `rules.term_policy_gates` in [Database concurrency](database-concurrency.md).

### Uniqueness, idempotency, retries, and deadlocks

Database active-enrollment uniqueness remains independent of key replay. Same-key requests resolve before domain locks; different keys for the same student still serialize on student-term state. Failed domain writes roll back completely, transient failures do not become terminal academic denials, and bounded retries restart the whole transaction using the original key.

[Enrollment design](enrollment-design.md#idempotency-key-lifecycle) owns key scope, expiry, concurrent duplicates and recovery. [Scaling strategy](scaling-strategy.md) owns admission/time-budget decisions. Physical constraints/indexes and hidden lock interactions are specified in [Database](database.md) and [Database concurrency](database-concurrency.md); actual PostgreSQL verification remains future work. No JVM mutex, Redis counter or cached validation is authoritative.

## Acceptance scenarios for later verification

These are specifications, not executed tests. Fixture numbers are supplied scenarios or proposed test inputs, not measured system capacity.

| ID | Scenario | Required outcome |
| --- | --- | --- |
| CT-01 | An initially empty 30-seat section; 2,000 distinct eligible students attempt enrollment in an open window; no withdrawals/admin changes. | At most 30 active enrollments at every committed state. In a healthy run where all attempts finish and deadlines are adequate, exactly 30 distinct students succeed and 1,970 receive `SECTION_FULL`. Any technical failures fail that allocation run; safety alone is insufficient. |
| CT-02 | Same student/section submitted concurrently with the same key, then with different keys. | Same key replays one outcome. Different keys cannot produce additional active enrollment or consume another seat; provisional offering uniqueness also holds across sections. |
| CT-03 | A student has room for either of two requested courses, but not both; requests race. | Exactly the admissible subset can commit; the other is denied, and the credit total remains within the limit. |
| CT-04 | A student concurrently requests sections with conflicting meetings. | Both cannot become active. Separate cases cover adjacent meetings, several meetings per section, nonoverlapping date ranges, and recurrence/timezone boundaries. |
| CT-05 | A failure occurs after seat mutation but before commit. | No enrollment, seat increment, credit change, or success result from that transaction survives rollback. |
| CT-06 | Commit succeeds but the HTTP response is lost; client retries the original key. | Original committed outcome is replayed without consuming another seat. A pre-commit crash allows safe re-execution. |
| CT-07 | Repeated/concurrent withdrawals race with a new eligible enrollment for the released seat. | Withdrawal releases once, occupancy remains accurate, and any re-enrollment obeys current window/rules with a new key. |
| CT-08 | Requests straddle open/close boundaries; an admin closes while enrollments wait/execute. | BR-11 is observed after lock waits. Once close commits, new validation decisions reject enrollment; already validated transactions are resolved before that close commits. |
| CT-09 | Admin attempts to reduce capacity below occupancy or change published academic facts during registration. | Invalid capacity is rejected under section protection; frozen academic facts are not changed in the live window. No admin bypass corrupts existing enrollments. |
| CT-10 | Deadlock/timeout, PostgreSQL outage, missing optional cache, and restart are injected. | No partial/duplicate mutations. Transient technical errors remain visible; missing Redis cannot authorize stale writes. Recovery and key replay behavior match the documented contract. |

## Assumptions and unresolved requirements

Rows remain open unless a partial Phase 2 resolution is stated. The suggested decision owners are roles, not assigned people. No answer is inferred from SDU's current implementation.

| ID | Topic and provisional assumption | Decision needed / suggested owner |
| --- | --- | --- |
| Q-01 | MVP resolved: synthetic test accounts and opaque PostgreSQL-backed sessions. Real university account integration is outside the MVP. | Remaining: test-session limits/credential distribution; future real identity provider, protocol and account lifecycle / security owner + university IT |
| Q-02 | Credit limits are configurable per student and term; no numeric limit assumed. | Credit units/precision, overrides, repeated courses and effective dates / registrar |
| Q-03 | Provisionally, only one active section per course offering and term. | Lecture/lab/tutorial bundles, cross-listed courses, repeats, section swaps / registrar |
| Q-04 | All required meetings use dated half-open intervals; a known institution timezone is mandatory. | Academic timezone, recurrence/week patterns, exam clashes, travel buffers, asynchronous classes / registrar |
| Q-05 | Prerequisites require versioned local academic records, frozen for the window. | Grades, equivalencies, transfer credits, concurrent requisites, waivers, import source and freshness / registrar + IT |
| Q-06 | Enrollment and withdrawal windows may differ; cohort rules are configuration. | Window calendar, administrative close semantics, late withdrawal and override policy / registrar |
| Q-07 | No waitlist, reservation, priority allocation, or fairness guarantee. | Whether any is required; related product scope and acceptance tests / product owner |
| Q-08 | Published academic facts are frozen while open; versions referenced by active enrollment remain immutable even after closing. | Whether this restriction is acceptable; safe live correction/revalidation procedure / registrar |
| Q-09 | Numeric load/latency/error targets remain hypotheses. | Traffic mix, durations, polling, opening burst, dataset size, timeouts, topology, resources and success cohort / tech lead |
| Q-10 | Recovery and personal-data obligations are unspecified. | Availability SLO, RPO/RTO, retention, audit access, applicable jurisdiction and backup policy / university IT + data owner |
| Q-11 | Phase 3 specifies UUID keys, transaction-local claims, terminal guards and old-operation-UUID cleanup/reuse under a level-1 row lock; no committed processing lease. | Remaining: supported retention duration, retry/response budgets and user-facing recovery beyond expiry / API owner |
| Q-12 | Two supplied application repos have no advertised refs; this local planning root is separate. | Authoritative documentation/infrastructure home, release coordination and API contract ownership / tech lead |
| Q-13 | Phase 3 specifies pre-provisioned student-term rows under an exclusive PREPARING term gate; missing records never trigger an unlocked fallback. | Remaining: operational provisioning ownership/runbook and final privilege approval / data owner + backend lead |

## Requirements review gate

Documentation completion does not mean policies are approved. Phase 2 refines the module and enrollment protocol in [Architecture](architecture.md) and [Enrollment design](enrollment-design.md), with discrepancies and risks listed as AF-01 through AF-12. Before implementation, resolve model-changing academic questions, review the [Phase 3 physical design and DBF findings](data-integrity.md), approve the complete writer/lock inventory, and agree on a benchmark workload. Phase 4 has now been separately authorized and its foundation checks executed; Phase 5 requires another instruction. Do not promote unverified targets to marketing or production capacity claims.
