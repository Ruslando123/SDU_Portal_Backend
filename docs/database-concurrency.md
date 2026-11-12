# PostgreSQL transaction and locking specification

Status: Phase 3 physical protocol implemented in Phase 5 with AD-13 late section locking, November 6, 2026. Illustrative baseline SQL below retains the historical early-lock sequence; the final section specifies the implemented ordering. V7 supplies the narrow student lock capability. See [verification](phase5-verification.md).

## Root transaction and physical resources

Use one PostgreSQL READ COMMITTED root transaction and one joined connection for each mutation. Enrollment, Course Sections, Academic Rules, technical idempotency and audit ports cannot independently commit or acquire a nested domain connection. Authenticate/authorize, validate CSRF and command shape, and perform bounded admission before acquiring this connection. Sessions are resolved/persisted in separate short framework transactions outside domain work; do not hold a session lock while waiting for a gate.

The physical resource order is:

1. **Claim/lock `operations.idempotency_records`** by the full principal/type/version/key scope. A new row is private until commit; replay/replacement locks an existing row before any domain resource.
2. **`rules.term_policy_gates(term_id) FOR SHARE`** for enrollment/withdrawal/capacity, or FOR UPDATE for close/provision/publication. No shared-to-exclusive upgrade.
3. **`enrollment.student_terms(student_id, term_id) FOR UPDATE`**, when a student is affected. No on-demand insert on the registration path.
4. **`sections.course_sections(section_id) FOR UPDATE`**. Future multi-section commands would acquire immutable IDs one at a time in sorted order; they are not part of this baseline.
5. **Enrollment mutation, audit and terminal result**. Updating the already owned claim at finalization does not acquire a different level-1 scope. A command never claims a second key after acquiring domain locks.

Point SELECTs lock only the intended base table; avoid an ORM fetch join with FOR UPDATE that silently locks unrelated parents. Do not infer row-lock order from an unordered IN/ANY result or application collection order. Stable rows/IDs exist before registration. A missing gate/student-term is a configuration/availability error, not an opportunity to proceed unlocked.

READ COMMITTED supplies fresh statement snapshots, not automatic student-wide serializability. Obtain the student-term lock in its own statement, then read that student's current ACTIVE episodes in subsequent statements. Gate protection freezes selection, and published course/meeting/eligibility versions are immutable. This makes compliant same-student writes serial while independent students remain concurrent until they share a section. PostgreSQL describes conditional rechecking and statement visibility in [Transaction Isolation](https://www.postgresql.org/docs/current/transaction-iso.html).

## Idempotency claim, replay and expiry algorithm

The unique key is `(principal_id, operation_type, operation_version, idempotency_key)`. Scope uses an authenticated account's immutable principal UUID, not a client student ID. The random client UUID is validated before entry. SHA-256 fingerprint covers the versioned command, typed target and canonical body. It excludes cookies, CSRF material, correlation ID and retry transport metadata. Generic target UUIDs intentionally have no domain FK at this stage.

An admin create command targets a typed collection with target_id NULL; its parent/body identifiers and canonical collection route participate in the fingerprint. Generate the new course/offering/section UUID only after winning the claim, then include it in the terminal response/audit. Do not put a freshly generated per-attempt target UUID into the fingerprint: otherwise an identical retry would falsely mismatch. Existing-resource commands require a target UUID; compare nullable collection metadata with null-safe equality. No new collection table is needed.

1. Attempt INSERT of a new operation UUID with state CLAIMED and all terminal fields NULL, using ON CONFLICT on the named scope DO NOTHING RETURNING operation_id. Only the exact supported scope conflict is handled; an unrelated unique/error condition aborts normally.
2. If inserted, this transaction owns the claim. Continue to the gate; finalization must occur before commit.
3. If not inserted, issue a **separate fresh statement** selecting the scope FOR UPDATE. The conflicting row may have committed after the INSERT snapshot. If cleanup deleted it before this SELECT, repeat the INSERT within the remaining deadline. Do not depend on a single INSERT/SELECT CTE seeing an otherwise invisible conflicting row. See [INSERT](https://www.postgresql.org/docs/current/sql-insert.html).
4. After obtaining the row, sample actual database wall time. A visible CLAIMED row is a defect: the selected deferred terminal guard disallows its commit. A valid conflicting operation either remains uncommitted and makes the unique check wait, or becomes a COMPLETED row.
5. If unexpired, compare fingerprint and target metadata. A mismatch gives `409 IDEMPOTENCY_KEY_REUSED` without disclosing/replacing the response. A match replays the terminal status/body after current authorization; end the replay transaction without domain locks or a second academic audit event.
6. If expired, replay is no longer guaranteed. Under the existing level-1 row lock, remove **that old operation_id only** through the restricted expiry helper and claim a new operation UUID for the same scope in the same transaction. Never rewrite the old terminal response in place. A concurrent insertion/reuser is arbitrated by the same unique scope and must repeat the visibility/expiry decision as necessary.
7. A conflict wait or retry loop that exhausts its budget aborts the root transaction and returns transient `REQUEST_IN_PROGRESS` or availability failure as appropriate. Do not store that transient result as a terminal business denial.

The selected contract is **reuse after expiry is allowed**, not permanent deduplication. Within retention the original outcome is immutable; after expiry the same key can represent a newly executed operation, including after a prior enrollment was withdrawn. Clients must not automatically resubmit an unresolved intention beyond the advertised recovery horizon. They first reconcile authoritative own state and request a deliberate new intention. Active-only uniqueness prevents simultaneous duplicates, not historical repeat effects after expiry. This resolves Q-11's lifecycle semantics without inventing its retention duration.

### Illustrative scope claim and visible-row lookup

Fragments below belong to the root transaction; they are not a complete runnable procedure. Terminal values are supplied only during finalization.

```sql
INSERT INTO operations.idempotency_records (
    operation_id, principal_id, operation_type, operation_version,
    idempotency_key, request_fingerprint, target_type, target_id,
    target_term_id, state, created_at
) VALUES (
    :new_operation_id, :principal_id, :operation_type, :operation_version,
    :key, :fingerprint, :target_type, :target_id,
    :target_term_id, 'CLAIMED', clock_timestamp()
)
ON CONFLICT (principal_id, operation_type, operation_version, idempotency_key)
DO NOTHING
RETURNING operation_id;

-- If no row was inserted: a separate statement, not the same CTE snapshot.
SELECT * FROM operations.idempotency_records
WHERE principal_id = :principal_id AND operation_type = :operation_type
  AND operation_version = :operation_version AND idempotency_key = :key
FOR UPDATE;
```

Unique checks can wait for an uncommitted conflicting insertion, then re-evaluate whether the other transaction committed/rolled back. That wait happens before student/section locks. PostgreSQL documents this in [Index Uniqueness Checks](https://www.postgresql.org/docs/current/index-unique-checks.html). Never use ON CONFLICT DO UPDATE to overwrite another operation's fingerprint/result.

The minimal stored enrollment/withdrawal response contains stable episode/section/term IDs, operation state and decidedAt; a denial contains only its approved code/message and decision/correlation identifiers. Exclude dynamic seat counts, a current schedule, transcript details and credentials. If correlationId is part of the stored body it remains the original operation's identifier on replay; the new HTTP attempt has its own safe diagnostic correlation. Phase 5 JSON fields and fixed, minimal outcome bodies are documented in [API specification](api-specification.md).

### Expiry cleanup and simultaneous reuse

The cleanup worker owns no gate, student, section or audit locks. Sample a server cutoff, select a bounded ordered batch of terminal rows expired by that cutoff FOR UPDATE SKIP LOCKED, then delete only those operation UUIDs with the same expiration/state predicate. Selection/deletion share one short cleanup transaction. SKIP LOCKED is suitable here for queue-like maintenance, **not** for skipping an occupied section/student lock and claiming that it is full; see [SELECT locking clauses](https://www.postgresql.org/docs/current/sql-select.html).

```sql
-- Maintenance design fragment only. Runtime DELETE uses the guarded helper.
SELECT operation_id
FROM operations.idempotency_records
WHERE state = 'COMPLETED' AND expires_at <= :server_cutoff
ORDER BY expires_at, operation_id
LIMIT :batch_size
FOR UPDATE SKIP LOCKED;
```

- **Replay locks first, checks before expiry:** it returns the original result even if wall time passes expiry while sending the response. Cleanup skips/waits on that row.
- **Reuser locks an expired row first:** it deletes the old UUID and claims a new one. Cleanup selecting the old UUID can never delete the new UUID merely because the scope matches.
- **Cleanup locks/deletes first:** INSERT waits/rechecks the scope, then may claim a new operation after cleanup commits. A preceding DO NOTHING followed by a missing SELECT loops rather than returning a fabricated result.
- **Reuser aborts after deleting/replacing:** the whole transaction rolls back, restoring the old expired row and removing the new claim. Another contender rechecks expiry normally.

The cutoff is never supplied by an untrusted client. Cleanup rechecks expiration and state after locking; it cannot delete an uncommitted/in-flight claim or an unexpired terminal outcome. No inbound FK from membership/audit to the expiring row blocks cleanup. Account anchors are retained; NO ACTION principal FKs are not an online account-deletion workflow.

## Exact enrollment transaction

1. Authenticate a synthetic student, derive its internal student ID, authorize, check CSRF/key/body bounds and admit the request. A locator read may discover immutable term/offering from section ID; it is a hint, and missing-target routing must not bypass a valid matching replay.
2. Execute the level-1 claim/replay algorithm. A new mutation continues with its private CLAIMED row.
3. Acquire shared term gate, then exclusive pre-existing student-term, then exclusive target section. Verify locator/term/offering against protected rows. Require PUBLISHED gate/offering/section state. Student-term policy/cohort/snapshot assignment is frozen; never select a new latest policy here.
4. After all waits, sample `clock_timestamp()` and record `decided_at`. Validate the pinned policy's ENROLL window: not administratively closed and `opens_at <= decided_at < closes_at`. Transaction-start `now()` would be stale after waiting and is not the decision clock. See [PostgreSQL Date/Time Functions](https://www.postgresql.org/docs/current/functions-datetime.html).
5. Validate COMPLETE/ACTIVE academic evidence, explicit offering/policy allowlist, and prerequisite AND-list with matching grade schemes. Read ACTIVE student-term memberships and SUM their immutable offering credits. Apply pinned default/override limit and check all required meeting overlaps. Same-student operations cannot change membership while this lock is held. No external calls or Student Schedule write authority are involved.
6. Check active section/offering duplicates before applying collected academic denials so an already enrolled student does not receive a self-conflict/credit message. All validations finish before any seat mutation.
7. Perform the conditional section increment and require one returned row. With identity/publication already verified under section lock, zero rows indicates full capacity. Timeout/connection/constraint failure is technical, not `SECTION_FULL`.
8. Insert a new ACTIVE episode with the protected student/term/policy/section/offering and ENROLL window IDs. Composite FKs bind those identities. Do not create a reservation or pending episode, and do not update a stored student credit counter because none is selected.
9. Append one ENROLLED audit event and finalize the same operation row to COMPLETED with stable HTTP result, minimal JSON, decision time and configured expiry. Expiry must exceed decision time; retention configuration must cover the supported client recovery horizon and bounded transaction duration.
10. Commit once at the root. The deferred final-state guard requires a completed result. Only after acknowledged commit may HTTP return `201`. An uncertain commit acknowledgement is recovered by the same key, not assumed rollback.

```sql
-- In one READ COMMITTED root transaction after winning the claim:
SELECT term_id, configuration_state, revision
FROM rules.term_policy_gates WHERE term_id = :term_id FOR SHARE;

SELECT policy_id, cohort_code, eligibility_snapshot_id, credit_limit_override
FROM enrollment.student_terms
WHERE student_id = :student_id AND term_id = :term_id FOR UPDATE;

SELECT offering_id, term_id, capacity, enrolled_count, publication_status
FROM sections.course_sections WHERE section_id = :section_id FOR UPDATE;

-- Fresh reads/decision clock and all academic checks occur here, before mutation.
UPDATE sections.course_sections
SET enrolled_count = enrolled_count + 1
WHERE section_id = :section_id AND enrolled_count < capacity
RETURNING enrolled_count, capacity;
```

The explicit section lock and later conditional UPDATE deliberately overlap as selected in AD-03/AD-04. The UPDATE is the final allocation guard, not a replacement for student-wide protection. An eligible full-section denial commits audit plus terminal `SECTION_FULL` result only; no membership/count mutation occurs. A validation denial is a typed outcome, not a caught database exception in an already aborted transaction.

## Withdrawal, safe release and re-enrollment

Discover the episode's immutable owner/term/section through ownership-filtered query; claim the withdrawal scope, then gate S → student-term U → section U → episode mutation. Re-read ownership and state under protection. For ACTIVE episodes validate the pinned policy's WITHDRAW window using fresh post-wait wall time; a previously WITHDRAWN episode may return its existing state without releasing again.

Change ACTIVE to WITHDRAWN and require one affected row, then decrement the already protected section only when its count is positive and require one affected row. Store withdraw decision/window, append WITHDRAWN audit and terminal result, then commit together. If either affected-row check is wrong, rollback all changes as an integrity failure. A missing/zero count for an ACTIVE episode is not silently repaired. The order of the two SQL mutations does not expose a partial committed state because both are in the same root transaction.

```sql
-- Under gate/student/section protection, with a valid WITHDRAW window:
UPDATE enrollment.enrollments
SET status = 'WITHDRAWN', withdrawn_at = :decided_at,
    withdraw_window_id = :withdraw_window_id
WHERE enrollment_id = :enrollment_id AND student_id = :student_id
  AND status = 'ACTIVE'
RETURNING section_id;

-- Execute only for one successful ACTIVE -> WITHDRAWN transition.
UPDATE sections.course_sections
SET enrolled_count = enrolled_count - 1
WHERE section_id = :section_id AND enrolled_count > 0
RETURNING enrolled_count;
```

Two withdrawals for one student serialize before touching the episode. A new student's allocation can see the released seat only after withdrawal commits; rollback exposes no release. A student's new enrollment after withdrawal is a new episode/new intention; the active-only indexes permit it, while the old episode/audit/replay result remains historical. More than capacity historical ENROLLED events can exist when withdrawals occur; the invariant concerns simultaneous ACTIVE episodes.

## Per-writer lock acquisition matrix

U = FOR UPDATE, S = FOR SHARE, KS = ordinary referenced-key protection from immediate FK checking. Ordinary reads also take relation AccessShareLock; SELECT row-lock statements take RowShareLock, and INSERT/UPDATE/DELETE take RowExclusiveLock on written relations. These compatible relation locks do not eliminate row/transaction-ID waits. Online DDL/parent deletion is excluded; its stronger relation locks need a maintenance plan. See [PostgreSQL Explicit Locking](https://www.postgresql.org/docs/current/explicit-locking.html).

| Writer | Level 1 | Gate | Student-term | Section | Later work / implicit parents |
| --- | --- | --- | --- | --- | --- |
| Enrollment | New unique claim or existing outcome U; principal KS | S by term | U by student/term | U by section | INSERT episode obtains KS on already owned student-term/section and on protected immutable window; INSERT audit obtains KS on stable principal and own episode; finalize owned claim |
| Withdrawal | Same scoped claim behavior | S | U | U | Conditional episode UPDATE, counter UPDATE; new withdrawal-window FK check under gate; audit actor/episode KS; finalize owned claim |
| Admin capacity update | ADMIN_CAPACITY_UPDATE claim first | S | Skipped | U | Check `config_version`, require new capacity >= enrolled_count, update capacity/config_version and audit/result; never bump gate revision under S |
| Registration window close | ADMIN_WINDOW_CLOSE claim first | U | Skipped | Skipped | Validate gate revision, UPDATE selected window to closed, bump gate revision, audit/result; no window lock acquired before gate |
| Student-term provisioning | ADMIN_PREPARE_STUDENT_TERM claim first | U, require PREPARING | Insert/lock known student-term only after gate; PK arbitrates duplicate setup | Skipped | FK KS on retained student, policy and evidence parents; idempotent existing identical assignment is allowed, mismatch denied; audit/result |
| Idempotency cleanup | Only terminal-expired outcome rows U SKIP LOCKED in bounded batches | None | None | None | Guarded delete by old operation UUID; no parent delete and no domain/audit FK cascade |
| Draft publication / fixture import | Approved admin claim first for each bounded command | U, require PREPARING | Sorted affected coordination rows only if needed | Sorted affected sections only if needed | Immutable parent KS for inserts; publication validates children then freezes versions. Large imports are offline bounded batches, not one giant enrollment transaction. |
| Authentication/session lifecycle | Separate short auth/framework transaction only | None | None | None | Account reads/non-key revocation; isolated session/attribute row/unique/FK locks; never waits on domain locks while holding auth locks |
| Reconciliation | Read-only short REPEATABLE READ snapshot | No row lock | No row lock | No row lock | Query academic membership/counters/audit; detection only, not an implicit repair writer |

Each command follows only a prefix/subsequence of the global order, without upgrades or returning to an earlier resource. A capacity command never waits for a student after locking the section. A close command never holds a window/student/section while waiting for its exclusive gate. Provisioning cannot run as a registration fallback.

For capacity edits, compare the section's protected config_version with the admin precondition and reject a proposed capacity below the protected enrolled_count; increment only section config_version. For close, compare the protected gate revision, close only the selected policy/operation window and increment gate revision under its existing U lock. Repeated already-closed commands have no second state-change event. Provisioning reuses an identical pre-existing student-term assignment in PREPARING and rejects a conflicting one rather than silently rebinding it. All three commands store their own terminal result/audit through the same technical adapter before the single root commit.

## Foreign-key and unique-index side effects

| Reference / constraint | Possible hidden wait | Design response |
| --- | --- | --- |
| Claim → test account, then audit → actor | KS on principal anchor can conflict with account key changes/deletion or an explicit U lock | Principal/mapping keys are retained/immutable; normal enable/auth_version writes are non-key updates. Account/session maintenance is a separate no-domain-lock transaction, never a holder waiting for a gate. |
| Claim target UUID | An FK to section/episode would acquire a domain key lock at level 1 | No target FK. Verify after ordered locks; missing-target denials remain representable. |
| Episode → student-term composite key | Late KS would conflict with another transaction's U on that same student-term | This transaction already owns U before INSERT; all other membership writers must acquire it before touching this student's episode. |
| Episode → section/offering/term composite key | Late KS on section can conflict with U allocation/capacity work | Already own target section U; FK ensures identities agree. Do not update referenced ID/offering/term columns online. |
| Episode → enrollment/withdrawal window | KS can conflict with window close/update | Every window writer obtains gate U first; current enrollment/withdrawal already holds gate S. Window close cannot hold its row and then request the gate. |
| Student-term → policy/evidence/student; section → offering/term; eligibility allowlist → offering/policy | Parent key updates/deletes could introduce backward waits | All referenced academic anchors/versions are retained; setup/publication/import takes gate U before child writes. No concurrent multi-term rekey/cascade workflow is admitted. |
| Audit → stable enrollment PK | KS on episode, especially during a withdrawal | The writer already owns the student/section ordering and episode mutation; do not target an active partial index or mutable status key. |
| Partial active section/student and student/offering uniqueness | May wait on a concurrent uncommitted insert/removal even when a prior read saw no row | Same-student U normally resolves it before the index; uniqueness is a backstop, not a replacement for the protocol. Any unexpected violation after a seat increment rolls back the complete root. |
| Audit → operation correlation | An FK to an expiring result would block cleanup or tempt history cascade | No FK; retain audit operation UUID as immutable evidence, independent of replay retention. |
| Session attributes → session internal PK | Parent deletion/attribute update can wait or deadlock in framework maintenance | Keep standard isolated session transactions; session-ID rotation changes a unique key but no academic resource is involved. Retries/cleanup remain separate from domain writes. |

KS is referenced-row protection, not a lock of an entire parent table. PostgreSQL's referential-integrity implementation performs referenced-key locking; review the selected version's [RI trigger source](https://github.com/postgres/postgres/blob/REL_18_STABLE/src/backend/utils/adt/ri_triggers.c) and real two-connection experiments before claiming the physical protocol verified. Do not rely on an ORM deciding to skip an unchanged FK check. Nonpartial referenced keys remain valid after withdrawal; active partial unique indexes cannot be FK targets.

The role of an immutable anchor is to avoid conflicting online mutation, not to pretend a late FK lock is absent. This inventory covers the selected writers. Adding a new parent lifecycle, raw SQL import, callback or cross-term operation requires extending the inventory first.

## Deadlocks, rollback, crashes and response handling

Potential cycles include (a) section → student versus student → section, (b) shared gate upgrades by two capacity commands, (c) window-row-first close versus gate-first enrollment, (d) account-lock-plus-gate maintenance versus late audit/claim FK, and (e) expired-scope cleanup deleting by scope after a new operation replaced it. The selected ordering, no upgrades, isolated account work and operation-UUID cleanup remove these known inversions from compliant paths. FK/index/trigger interactions still require later PostgreSQL tests; no claim of zero deadlocks is made.

On SQLSTATE 40P01 or a genuine 40001, rollback the **whole** root and claim, then retry a fresh transaction with the same key using bounded jitter and total elapsed/attempt budgets. On lock/statement timeout, release/rollback before mapping a retryable technical failure; do not transform saturation into a business denial. Unexpected unique/FK/CHECK/trigger failure rolls back the entire seat/episode/audit/result unit. Only a recognized duplicate is re-evaluated in a fresh transaction; unknown constraints indicate a defect.

- Before commit, a backend crash aborts the private claim and all domain effects once disconnect/transaction timeout is detected. Locks may last until detection; no time-based claim stealing is allowed.
- After durable commit but before response, membership/audit/result survive and another instance can replay. A failed response serializer or session-save step does not justify withdrawing that committed enrollment.
- Lost commit acknowledgement is ambiguous, not proof of rollback. Resolve through same-scope retry after database/auth recovery.
- A stored business denial is terminal within retention. Replaying it after a seat becomes available still returns the denial; deliberate reconsideration needs a new intention.

Natural window closing is checked at post-lock decision time, not commit time. Admin close waits for shared holders and, if it commits, later protected decisions observe its state. It has its own finite deadline; timeout means close failed, not that registration secretly closed. Runtime pools/queues/lock waits are bounded per [Scaling strategy](scaling-strategy.md), with numerical settings still unmeasured.

## Capacity proof obligation and review gate

Start with empty ACTIVE ledger and zero counters, validated before gate publication. Every successful enrollment adds one membership and one count while holding section U; every successful withdrawal removes one active membership and one count under the same lock; every failed root changes neither. Capacity updates hold section U and cannot set capacity below count. This inductive argument explains the intended equality and bound for **compliant** writers. The database row CHECK enforces the bound, active partial uniqueness enforces duplicate safety, and protocol plus later tests enforce aggregate equality/academic rules.

For CT-01's initially empty 30-seat fixture, at most 30 ACTIVE episodes can commit; a healthy complete run of 2,000 eligible attempts without withdrawals/admin changes expects 30 successes and 1,970 SECTION_FULL denials. Technical failures fail the complete-allocation scenario even if the safety bound holds. Phase 5 executed this complete-allocation fixture through the public service with 32 workers and a pool of 16: 30 successes, 1,970 SECTION_FULL, no technical failures and zero checked violations. [Data integrity](data-integrity.md) defines the independent invariant queries and additional verification plan to review before Phase 4; Phase 4 executed the selected primitive/schema cases in that plan; Phase 5 now provides the core use-case evidence in [its report](phase5-verification.md). HTTP load capacity still requires later authorization.

## Phase 4 late section lock candidate

Status: adopted and implemented in Phase 5. The early-section sequence above is the historical baseline, not a second runtime option. The real order was tested under runtime credentials, including concurrent applications and post-wait natural closing.

1. Authenticate/admit and claim the supported idempotency scope.
2. Acquire the shared term gate, then exclusive student-term row in the unchanged global order.
3. Read the target's immutable offering/meeting data, pinned policy/evidence, and the student's current ACTIVE memberships. Compute prerequisites, exact credit totals, all meeting clashes and duplicate findings without section U. Use batched ordinary reads; no hidden ORM FOR UPDATE or foreign internal repository access.
4. Acquire target section U immediately before final mutable-state validation/allocation. Own membership remains protected while waiting; do not release student U or gate S.
5. Revalidate section/offering/term/publication, sample a fresh database wall clock, and evaluate the correct current window and all time-sensitive eligibility freshness against that clock. Capacity/count use the newly protected row, never an earlier display/read. Finalize denial precedence only now so moving queries does not silently change observable rule ordering.
6. Apply the conditional allocation, episode/audit/terminal-result changes and one root commit as before. Any failure rolls everything back. No module gains an independent commit.

Safety depends on the existing frozen published/eligibility lifecycle and complete writer inventory. Admin capacity can change between ordinary reads and section U and is therefore rechecked; admin close/configuration cannot pass gate S, while natural closing is still detected by the post-wait clock. The selected tests demonstrate these properties and student-lock visibility, not complete validation of every future academic rule or an improvement in throughput. The schema and active uniqueness are unchanged.

## Phase 5 runtime transaction boundary

EnrollmentService rejects an ambient transaction, validates the server-owned student/key and acquires a bounded local permit. It invokes the separate Spring-proxied EnrollmentAttempt.execute method. That interceptor owns READ COMMITTED and commits before returning to the orchestrator. JDBC adapters/technical outcomes require MANDATORY propagation on the same auto-configured JdbcTransactionManager and Sql/JdbcTemplate datasource; no manual domain connection, REQUIRES_NEW, self-invoked transaction entry or external service exists. A test checks one manager and identical PostgreSQL xmin for seat, episode, audit and result.

F5-01: V5 intentionally grants no student_terms UPDATE, so a raw SELECT FOR UPDATE fails as portal_runtime. V7 adds enrollment.lock_student_term(student,term), owned by portal_owner, SECURITY DEFINER, fixed pg_catalog search_path and PUBLIC execution revoked. It takes the same student FOR UPDATE lock on the caller's connection/transaction and returns only the pinned assignment. Runtime gains no academic UPDATE privilege. The populated V6-to-V7 upgrade is independently tested; V1–V6 are unchanged.

The isolated MVP budgets are local admission 64, three fresh attempts, 15-second total deadline, 10-second root timeout, lock wait up to 2 seconds and statement time up to 5 seconds capped by remaining deadline. Pool wait is 3 seconds normally (1 second in the fast integration profile). A slow commit/transport acknowledgement can outlive a client budget and remain ambiguous; reuse the same key. Business denials commit; missing/unavailable evidence, timeout, pool/admission failure and SQL errors roll back. Only 40P01/40001 trigger automatic bounded retries. Missing evidence is 503, never a fabricated passed prerequisite or durable academic denial.
