# Data integrity, security, fixtures, and review plan

Status: Phase 3 integrity plan reconciled with Phase 5, November 6, 2026. Foundation tests, core service/HTTP tests and the controlled 2,000-student allocation have executed on PostgreSQL; [Phase 5 verification](phase5-verification.md) distinguishes that evidence from the remaining DBV/admin/performance scope. Only synthetic data is used.

## What the database enforces versus the protocol

| Requirement | Direct PostgreSQL mechanism selected | What still requires transaction/application logic and tests |
| --- | --- | --- |
| BR-01 local seat bound | NN integer capacity/count and CHECK `capacity >= 0 AND enrolled_count >= 0 AND enrolled_count <= capacity` | Correct initial values, atomic allocation/release and safe capacity edits |
| BR-01 count equals membership | No cross-table CHECK and no aggregate counter trigger | Under section U, change membership and count together in one root; initialize/reconcile/test independently |
| BR-02 duplicate section | Immediate unique partial index `(section_id, student_id) WHERE status = 'ACTIVE'` | Typed duplicate outcome and rollback after unexpected conflict |
| BR-02 provisional duplicate offering | Immediate unique partial index `(student_id, offering_id) WHERE status = 'ACTIVE'` | Q-03 approval; linked components would change this constraint/model |
| Same student/term and coherent references | Student-term PK; composite episode→student-term/policy and episode→section/offering/term FKs; same-student evidence FK | Ownership authentication, correct selected window operation type and immutable assignment lifecycle |
| BR-03 credit limit | Exact nonnegative numeric credits/limits and FK to pinned policy | SUM ACTIVE offering credits after student U; apply override; concurrent different-section requests share that lock |
| BR-04 schedule conflicts | NN meeting bounds and CHECK ends_at > starts_at, uniqueness of identical section occurrences | Dated half-open overlap evaluation under student U; batch all meetings; publication rejects self-overlap and missing required meetings. No global section-overlap exclusion. |
| BR-05 prerequisites/eligibility | Structured immutable references, explicit COMPLETE/UNAVAILABLE evidence, grade-pair shape checks and offering/policy allowlist PK | Evaluate AND-list, pass/minimum/grade scheme, source freshness and academic status; no inferred SDU rules |
| BR-06 atomicity | One ACID transaction, constraints abort invalid writes | Joined connection/transaction at all module ports; audit/result failure rolls back every domain change |
| BR-07 idempotency | Unique principal/type/version/key scope, terminal field-shape CHECK, selected deferred final-state guard | Canonical fingerprint, semantic terminal-code allowlist, current authorization, cleanup/replay decision and supported recovery horizon |
| BR-09 withdrawal/history | Episode shape CHECK, stable PK/FKs, selected one-way episode transition guard | Release once under student/section protection, preserve old episode/audit and use a new intention/episode for re-enrollment |
| BR-10 publication | Stable IDs/composite references and retained versions | Guarded PREPARING publication lifecycle, frozen parent/child facts, no unreviewed correction/import path |
| BR-11 window timing | NN timestamps and closes_at > opens_at; explicit close-flag shape | Shared/exclusive term gate, correct cohort/operation and actual post-wait database wall time |
| SEC-01 through SEC-06 | Schema/table/column privileges, retained principal anchors and restricted session/audit storage | Endpoint/use-case ownership/role/CSRF checks, redaction, finite rate/work limits, real revocation and session handling |

PostgreSQL's [Constraints](https://www.postgresql.org/docs/current/ddl-constraints.html) do not turn a row CHECK into an aggregate assertion over another table. Do not hide a COUNT query in a CHECK-called function and claim continuous enforcement. The selected model deliberately makes the protocol boundary explicit.

## Implemented narrow lifecycle guards

These preserved contracts are implemented by Flyway V4/V5 and tested in Phase 4:

1. **Episode update guard:** BEFORE UPDATE, reject changes to enrollment identity/owner/term/policy/section/offering/enroll-window/enrolled_at. Permit only ACTIVE → WITHDRAWN with matching withdrawal fields; do not allow resurrecting a withdrawn episode. A repeat withdrawal performs no UPDATE and returns existing state. This is row-local and takes no new domain locks.
2. **Idempotency transition guard:** claim scope/fingerprint/target/created_at are immutable; permit one CLAIMED → COMPLETED finalization, with row-shape checks. A completed record cannot be refreshed/rebound/replaced in place. Expiry reuse removes the old UUID through the restricted helper and inserts a new one.
3. **Deferred terminal guard:** an AFTER INSERT/UPDATE constraint trigger, DEFERRABLE INITIALLY DEFERRED, inspects the operation's current row by operation_id at transaction end. Require the row to exist and be COMPLETED. The queued insertion NEW image can still say CLAIMED, so checking that stale image would incorrectly reject legitimate work. It reads only its own operation row and never acquires gate/student/section locks or commits. Cleanup DELETE of already committed terminal rows creates no new insert/update event.
4. **Expired-outcome deletion helper:** owner-controlled, narrowly granted function locks/rechecks only terminal rows whose expiry has passed; delete by immutable old operation UUID. A batch wrapper uses a bounded ordered SKIP LOCKED selection. Neither helper commits, touches domain data, nor deletes CLAIMED/unexpired rows. The caller owns the transaction.

No trigger increments/decrements seats automatically, and no aggregate equality trigger is selected. Adding one later would require removing duplicate application accounting and reviewing late lock acquisition. Published parent/child immutability is enforced by the owning write APIs and PREPARING gate lifecycle; a broader database publication-guard family can be considered only with its own explicit lock review. The runtime is a trusted service boundary, not protection against arbitrary malicious SQL executed with all its DML privileges.

## Roles, privileges, and direct mutation protection

Schema namespaces do not create database-enforced isolation between code modules sharing one connection. Module APIs/static architecture checks enforce that boundary. Database privileges protect the database from browser/end-user access, human ad-hoc changes and accidental broad runtime powers.

| Proposed role | Minimum responsibility | Explicit exclusions |
| --- | --- | --- |
| `portal_owner` NOLOGIN | Own reviewed schemas/tables/guards/helpers | Not a service or human login |
| `portal_migrator` | Controlled ownership/DDL through an explicit deployment path | Never used as the runtime datasource identity; local Phase 4 CLI/startup Flyway uses it explicitly |
| `portal_runtime` | Approved SELECT, required INSERT, and narrow mutable-column UPDATE through owning adapters; append audit; standard session adapter operations; EXECUTE guarded expired-outcome removal | No SUPERUSER/CREATEDB/CREATEROLE/DDL/TRUNCATE/trigger-disable/BYPASSRLS, no academic DELETE, no audit UPDATE/DELETE, no unrestricted idempotency DELETE |
| `portal_cleanup` | EXECUTE bounded expired-outcome helper; separately approved session expiry work | No direct enrollment/count/policy/audit mutation; helper cannot delete an unexpired result |
| `portal_fixture` | Explicit isolated synthetic setup/reset while registration is closed/PREPARING | Not an online registration/repair credential; never production data import |
| `portal_observer` | Approved operational stats and curated redacted diagnostic views | No credential/session/replay payload access or academic DML |

Revoke PUBLIC schema CREATE and unintended table/function privileges. Give definer helpers a fixed safe search_path with fully qualified references, no dynamic SQL, owner-controlled schemas and REVOKE PUBLIC EXECUTE. Validate scope/expiry/batch inputs internally. Do not expose database credentials to Next.js/browser or reuse migration credentials in application settings. A stolen privileged owner can defeat controls; no tamper-proof claim is made.

Account disable/auth_version changes and session rotation/logout are isolated short authentication transactions. Store only encoded synthetic test-password verifiers, never plaintext passwords. Session IDs/attribute bytes/replay bodies are sensitive database data: restrict reads, logs and backups. Framework principal_name is an index hint; current account mapping/enabled/auth_version checks are still mandatory. Avoid arbitrary serialized objects, transcripts or verifiers in session attributes; choose an allowlisted serializer/state representation before bootstrap.

No real SDU student information, real university credentials or actual instructor identities are used. Unknown production privacy/backup/retention obligations remain Q-10, not implied legal compliance.

## Deletion, history, and audit semantics

- Enrollment is not hard-deleted on withdrawal. Its ACTIVE → WITHDRAWN transition and original relationships remain; a new enrollment uses a new UUID. Runtime cannot reverse status to reuse the old row.
- Course/offering/section/term/student/policy/evidence identities and published facts are retained; no online rekey or parent deletion/cascade is admitted. Draft deletion/reset, if later required, needs a dedicated bounded lifecycle with its FK/lock analysis.
- Audit is append-only for runtime. Episode rows plus audit replace a redundant enrollment_history copy. Replays produce safe diagnostics without a second academic state-change event.
- Generic audit targets can represent missing resources/denials and preserve historical IDs; actual enrollment transition events have a real enrollment PK FK. Audit operation UUIDs intentionally outlive the deleted idempotency response without an inbound FK.
- Session attributes are ephemeral and may cascade only with their own session row. This isolated cascade never reaches academic state. Session cleanup is not a domain transaction.
- No live counter repair is authorized. Reconciliation detects drift; remediation closes admission, establishes an exclusive reviewed maintenance boundary, investigates cause and preserves audit before any separate repair task.

## Independent invariant query examples

Design fragments only; **not run**. A single statement sees a coherent statement snapshot. A multi-query invariant report uses one short read-only REPEATABLE READ snapshot on primary PostgreSQL or pauses workload for a quiescent final report. Do not compare a counter from one committed snapshot with a count from another and call the transient mismatch drift.

```sql
-- Must return zero rows in a valid fixture, including empty sections.
SELECT s.section_id, s.capacity, s.enrolled_count,
       COUNT(e.enrollment_id) AS actual_active
FROM sections.course_sections AS s
LEFT JOIN enrollment.enrollments AS e
  ON e.section_id = s.section_id AND e.status = 'ACTIVE'
GROUP BY s.section_id, s.capacity, s.enrolled_count
HAVING s.enrolled_count < 0 OR s.enrolled_count > s.capacity
    OR s.enrolled_count <> COUNT(e.enrollment_id);

-- Independent duplicate inspection; indexes must also reject this state.
SELECT section_id, student_id, COUNT(*)
FROM enrollment.enrollments
WHERE status = 'ACTIVE'
GROUP BY section_id, student_id
HAVING COUNT(*) > 1;

-- Provisional Q-03 invariant.
SELECT student_id, offering_id, COUNT(*)
FROM enrollment.enrollments
WHERE status = 'ACTIVE'
GROUP BY student_id, offering_id
HAVING COUNT(*) > 1;
```

The later report also computes credit SUM per student-term against pinned effective limit; checks required meeting intersections (`left.starts_at < right.ends_at AND right.starts_at < left.ends_at`) for distinct active episodes; verifies coherent section/offering/term/policy references, valid terminal responses, one-way history and expected audit events. Publication separately rejects conflicting required meetings within a single section, malformed intervals, missing required occurrences and unsupported timezone/DST expansion. No COUNT audit test alone proves no temporary committed over-allocation: combine row bounds, writer protocol review, contention checks and consistent samples.

## Synthetic dataset project

These counts are **specified test inputs**, not measured capacity, real university data or official academic rules. Phase 5 generates only the separate 2,000-student/30-seat allocation fixture and small integration fixtures; the larger dataset plan below is not fully generated.

| Area | Proposed deterministic fixture |
| --- | --- |
| Students/accounts | Exactly 5,000 synthetic students and corresponding enabled STUDENT test accounts, plus a small separate set of synthetic ADMIN accounts |
| Courses | Exactly 250 stable subject identities, deterministic synthetic codes/titles; credits include zero/fractional boundary cases in dedicated correctness variants |
| Terms/offerings | TEST-A and TEST-B. TEST-A offers all 250 courses; TEST-B reuses 50 of those courses, for 300 total offerings and distinct term snapshots |
| Sections | Approximately 600, made exactly 600 for reproducibility: 500 in TEST-A and 100 in TEST-B, with two groups per offering |
| Capacity distribution | TEST-A: 100 groups × 20 seats, 200 × 30, 150 × 50, 50 × 80. TEST-B: 25 groups at each of 20/30/50/80. One designated 30-seat hot group starts empty. Capacity zero is a separate correctness boundary fixture. |
| Instructor assignments | Synthetic identities with ordinary and co-taught sections; several groups per subject and more than one teacher where needed; no real staff names |
| Meetings | Illustratively 14 teaching weeks with two required occurrences per week per section, giving 16,800 dated occurrences. Adjacent/overlapping/date-disjoint/multiple-meeting variants are separate explicit cases. |
| Time/calendar | Fixture-only dates/timezone are declared in the eventual generator manifest, e.g. Asia/Almaty for synthetic journeys. This is not confirmation of university academic timezone/calendar. No dates are derived from the developer host implicitly. |
| Policies/student terms | Explicit synthetic cohorts and both operation windows; 5,000 TEST-A student-term rows plus a 1,000-student TEST-B subset if the multi-term variant is enabled. Limits such as 18.00/24.00 credits are labeled test-only values, not SDU policy. |
| Eligibility | Every load student has an explicit COMPLETE snapshot, including known-empty histories; prerequisite graph is acyclic. Separate pre-publication fault variants include missing/UNAVAILABLE evidence, failed grades and mismatched grade schemes. |
| Popularity | Declared hot/warm/cold selection tiers with reproducible weights/seed. The distribution is a workload hypothesis, not observed SDU traffic. |
| CT-01 cohort | 2,000 distinct students fully eligible for the designated empty 30-seat section: no conflicting enrollment, sufficient credits, allowed policy/window and satisfied/no prerequisites |
| Existing memberships | Record exact initial ACTIVE/withdrawn distribution and coherent counters. Dedicated credit-limit variants include exactly-at-limit, room-for-one-of-two and zero-credit cases. |
| Replay/expiry/session fixtures | Controlled retained/expired outcomes, revoked/expired sessions, same-key/different-body pairs, and known audit history. Old terminal timestamps are seeded through fixture privileges; do not change the system clock or rewrite live immutable outcomes to simulate expiry. |

Future generator records seed/version, exact IDs/cardinalities, timezone/meeting expansion, popularity weights, starting ledger/counters and fixture variant. Reset occurs only between runs under isolated fixture ownership. The mixed-load plan must provide a lasting known-success cohort and account for finite seat inventory/net enrollment; a mostly sold-out run cannot establish successful-allocation latency. Dataset size alone is not a 5,000-user benchmark.

## Future verification matrix

All rows are plans. Existing CT identifiers are preserved; DBV identifiers add physical-model coverage.

| ID / trace | Scenario | Planned assertion |
| --- | --- | --- |
| DBV-01 / CT-01 | 2,000 eligible students compete for 30 initially empty seats without withdrawal/admin changes | Never exceed 30 active members; healthy fully completed run gives exactly 30 distinct successes and 1,970 SECTION_FULL. Technical failures fail complete-allocation acceptance. Counter/ledger report is empty. |
| DBV-02 / CT-02 | One student sends two requests with the same key, and separately different keys/sections of the same offering; an admin retries a collection create | Same key executes once/replays, mismatched body conflicts; different keys cannot bypass section/offering active uniqueness. Admin create replays the original generated resource ID instead of fingerprinting a new random ID per attempt. Include routing to separate backend instances later. |
| DBV-03 / CT-03, CT-04 | Concurrent different offerings jointly exceed credits or overlap required meetings | Only an admissible subset commits, with fresh post-student-lock reads. Adjacent/date-disjoint meetings remain allowed. |
| DBV-04 / CT-07 | Double withdrawal under same and different keys | One ACTIVE→WITHDRAWN transition, one decrement, one academic change event; later replay/existing-state response does not release again. |
| DBV-05 / CT-07 | Enrollment and withdrawal race for a full/last-seat section, including same-student re-enrollment | New allocator sees only committed release, count/ledger agree; re-enrollment creates a new episode and all rules are rechecked. |
| DBV-06 / CT-05 | Error/crash after increment or episode insert but before commit, including audit/result write failure | Membership/count/audit/result all roll back; claim does not persist; restart/retry uses original key safely. |
| DBV-07 / CT-06 | Commit succeeds, then backend dies/response or session save fails | No compensation or second allocation; another instance returns original terminal outcome after current authentication. Unknown commit acknowledgement is resolved by replay. |
| DBV-08 / CT-02, CT-06 | Replay after withdrawal or after a stored full-section denial becomes eligible | Replay remains historical and unchanged; only a deliberate new intention can reconsider; no row resurrection. |
| DBV-09 / Q-11 | Expiry boundary with concurrent replay, reuser and cleanup worker | One defined row-lock winner, no cleanup of replacement UUID, no deletion of unexpired/CLAIMED work. Rollback of replacement restores old expired row. No replay guarantee after expiry is falsely promised. |
| DBV-10 / CT-08, CT-09 | Admin capacity increase/decrease and window close compete with registration | New capacity never below occupancy; no gate S→U upgrade; close obtains gate first and only acknowledged close prevents later decisions. Boundary time sampled after waits. |
| DBV-11 / CT-10 | Controlled deadlock, lock/statement timeout, exhausted pool and PostgreSQL disconnect | Whole-root rollback and bounded same-key fresh retry; no partial state or fake SECTION_FULL. Counter/ledger and terminal invariants hold after recovery. |
| DBV-12 / FK/shape constraints | Wrong section/offering/term, wrong-student evidence, wrong cohort-policy, invalid null state or duplicate active tuple | Exact named FK/CHECK/unique rejects the invalid state. Stable episode audit references remain valid after withdrawal and ordinary result cleanup. |
| DBV-13 / lifecycle guards | Commit an unfinished claim, alter terminal response, mutate episode identity or resurrect WITHDRAWN | Selected final-state/transition guards reject it; a legitimate claimed→completed transaction commits without reacting to stale NEW state. |
| DBV-14 / NFR-04 | Deliberate in-bound counter drift in an isolated disposable fixture using a privileged test actor | Demonstrate that a row bound alone can pass while aggregate equality fails; independent report detects drift. This is not an allowed runtime repair path. |
| DBV-15 / SEC-01 through SEC-06 | Unauthorized DB role writes, direct audit edits, real-account routes, revoked sessions, cross-student replay/withdrawal, CSRF failures | Roles/HTTP checks deny access; curated diagnostic views omit secrets; no real SDU connection exists. Session attributes cannot escalate trusted roles. |
| DBV-16 / Q-13 | Concurrent student-term setup and gate publication; insert-on-miss during registration | Setup occurs under gate U in PREPARING, duplicate identical setup is controlled, mismatch rejected; no missing-row registration fallback. |

For future verification beyond Phase 5, report actual artifacts: schema/application versions, synthetic seed, lock/statement traces, raw outcome counts, invariant query results and limitations. Executed Phase 5 results are recorded separately; no 5,000-VU load script has run.

## Performance bottlenecks to measure later

| Risk | Later measurements / first response |
| --- | --- |
| Hot section row | Section lock wait distribution, active transaction/pool occupancy and allocation rate; shorten protected work, bound admission; replicas cannot parallelize one counter row |
| Student-term contention | Same-principal duplicate rate, time waiting on coordination, replay rate; preserve one-student serialization and rate-limit repeated submissions |
| Excess locks / hidden RI waits | Actual held/waiting resources, FK parent and unique transaction waits, deadlock victims; compare observed trace to writer inventory before changing order |
| Pool exhaustion | Aggregate Hikari active/pending/acquisition time, queue rejection, DB CPU/I/O and administrative headroom; tune measured concurrency rather than user count |
| Slow enrollment queries / N+1 | SQL statement count per attempt, batched active memberships/offerings/meetings and relevant completions, query plans/rows/buffers; avoid a query per meeting/course in a lazy ORM loop |
| Index/write overhead | Insert/withdrawal WAL, index sizes, dead tuples/vacuum, update churn from status predicate changes; do not index count/capacity or speculative JSON searches |
| WAL/commit latency | Root protected time versus commit acknowledgement, storage latency and transaction rate; preserve durability and transactional audit rather than disable safety settings |
| Session/denial/audit load | Session refresh writes, terminal-denial/result retention, cleanup batch interference, audit volume and serialization; include these costs in the baseline |
| Expanding meetings/data volume | Occurrence cardinality, batch-query work, future simultaneous terms and history growth; add a measured index/retention strategy only for demonstrated need |

Use [Performance](performance.md) to distinguish users, RPS and simultaneous HTTP requests. The 5,000-active-student, latency/error and zero-observed-violation targets remain unverified. These plans are not results for the Windows/i5 workstation or SDU production.

## Phase 3 findings and architecture risks

AF-01 through AF-12 remain historical Phase 2 findings. DBF rows preserve the Phase 3 review state without renumbering earlier records; Phase 4 resolutions and executed evidence are recorded in [verification](phase4-verification.md).

| ID | Finding / decision | Remaining risk or review action |
| --- | --- | --- |
| DBF-01 | A claim FK to section/enrollment would take a domain lock before the gate/student order | Keep generic non-FK target identity, then verify under protection; explicitly document this intentional integrity boundary |
| DBF-02 | Aggregate occupancy equality cannot be expressed as a row CHECK | Choose protocol-maintained counter with independent verification; every new writer/repair must extend the inventory |
| DBF-03 | Active partial unique indexes cannot support historical audit FKs | Use stable episode PK and ordinary composite reference keys; accept documented extra integrity-index cost |
| DBF-04 | A FK from audit/episode to expiring result would block deletion or threaten history | Persist logical operation UUID without that FK; cleanup/reuse addresses only the old result UUID |
| DBF-05 | Separately committed processing claims and in-place TTL reuse introduce lease/cleanup ambiguity | Keep private claims, deferred terminal guard and delete/new-UUID expiry protocol. Actual retention and retry budgets remain Q-11. |
| DBF-06 | A custom session table would not be drop-in Spring Session JDBC storage | Select its standard two-table format; nullable principal metadata/serialization still needs security review and adapter version matching |
| DBF-07 | Per-module duplicate admin/student idempotency tables would repeat concurrency logic | Share a technical operations adapter/table, preserving domain scope ownership and public boundaries; it is not a new service or bypass into Enrollment internals |
| DBF-08 | Updating gate revision from a shared-lock capacity command would require an unsafe lock upgrade | Capacity has its own config_version; only exclusive-gate configuration commands change gate revision |
| DBF-09 | Cross-row publication validity and parent/child freeze are not fully enforced by ordinary row constraints | Keep PREPARING/exclusive-gate publication and trusted owning APIs; review any new direct writer or database guard family before enabling it |
| DBF-10 | Physical same-term multiple offering revisions/component bundles are not defined by approved academic rules | Baseline one immutable offering per course/term with bundled meetings; Q-03/Q-08 can require a model revision before code |
| DBF-11 | Schema privileges cannot isolate code modules sharing a trusted runtime role, and account/session work adds auxiliary locks | Enforce code boundaries separately; keep auth transactions isolated, immutable anchors and no online parent rekeys/deletes; test actual RI/unique behavior |
| DBF-12 | No runtime/DDL execution has validated this physical proposal | Review composite FK targets, guards/cleanup, session adapter compatibility and test plan before Phase 4. Capacity claims await later real PostgreSQL/k6 evidence. |

## Open academic assumptions and Phase 4 recommendation

Still unresolved: official credit units/precision/ceilings and overrides (Q-02); independent lecture/lab selection, repeats/cross-listing/swaps (Q-03); timezone/recurrence/exams/travel/asynchronous classes (Q-04); grade schemes/equivalencies/OR/corequisites/waivers and evidence freshness (Q-05); multiple/reopened/late windows and cohort policy (Q-06); allocation fairness/waitlists (Q-07); live academic corrections (Q-08). The documented AND rules, bundled sections, allowlist and two-decimal credits are **provisional project choices**, not statements of SDU policy.

Also decide test credential/session settings (Q-01), measured resource/workload budgets (Q-09), backup/RPO/RTO/audit retention (Q-10), replay retention/limits (Q-11), shared repository ownership (Q-12) and provisioning ownership/runbook (Q-13). Physical provisioning/expiry mechanics are now specified, but their operational values are not invented.

Phase 4 now implements the approved foundation/migrations and selected real-PostgreSQL verification. Its [report](phase4-verification.md) records F4-01 finite-number hardening and remaining gaps. The provisional bundled-section/one-offering/AND model was accepted for the MVP, not as SDU policy. Phase 5 was subsequently authorized and implements synthetic auth, core enrollment/withdrawal and AD-13. F5-01 required the narrow V7 lock helper; no broader runtime academic UPDATE permission was added. [Phase 5 verification](phase5-verification.md) records results and remaining limitations. Stop after Phase 5 for review; frontend, administrative breadth and performance work remain future scope.
