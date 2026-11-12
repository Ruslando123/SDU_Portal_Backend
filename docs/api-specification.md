# Initial API specification

Status: Phase 5 core HTTP contract implemented and tested, November 6, 2026. The implemented-contract section is authoritative for current paths/fields; the broader proposed surface is retained for later product work. [Phase 5 verification](phase5-verification.md) records evidence and limits.

## Implemented foundation boundary

The three health endpoints and synthetic login/CSRF endpoints are permitted anonymously, with health details hidden. Other routes use explicit role checks or deny-by-default. No default login password or real SDU integration exists. Positive authentication and the core student surface are implemented as specified below.

## Boundary and conventions

Proposed base path: `/api/v1`. The browser accesses UI and API through the same origin. JSON is the proposed data format. The MVP uses predefined synthetic test accounts and opaque server-side sessions; no real SDU authentication is connected. The backend derives student identity and role from authenticated context; a student cannot choose another student's identity in a mutation body. Resource identifiers do not provide authorization. See [Security architecture](security.md) for the authoritative session/CSRF boundary; the implemented login/session/logout routes are specified below.

List operations have server-bounded pagination and input lengths; exact limits remain TODO. A term identifier is explicit for catalog/schedule reads. Timestamps carry an offset or use UTC instants; schedule responses also identify the academic timezone. A seat snapshot includes its observation time and cache status where applicable.

## Proposed surface

| Method and path | Caller / purpose | Requirements |
| --- | --- | --- |
| `GET /me` | Synthetic authenticated principal and effective role; test login/logout paths remain to be specified | FR-01 |
| `GET /terms` | Student: published academic terms | FR-02 |
| `GET /courses?termId=…` | Student: searchable, paginated published catalog | FR-02 |
| `GET /courses/{courseId}/sections?termId=…` | Student: published sections, meetings and availability snapshots | FR-03, FR-07 |
| `GET /me/enrollments?termId=…` | Student: own enrollment states | FR-04, FR-05 |
| `GET /me/schedule?termId=…` | Student: authoritative active meetings and credit total | FR-06 |
| `POST /me/enrollments` | Student: enroll using `sectionId`; term is validated from the section | FR-04, FR-08 through FR-10 |
| `DELETE /me/enrollments/{enrollmentId}` | Student: own withdrawal with a returned terminal state | FR-05 |
| `GET /registration-windows?termId=…` | Student: applicable windows; display is advisory | FR-12 |
| `POST /admin/courses`, `PATCH /admin/courses/{id}` | Admin: draft course definition | FR-11 |
| `POST /admin/offerings`, `PATCH /admin/offerings/{id}` | Admin: term offering and publication lifecycle | FR-11 |
| `POST /admin/sections`, `PATCH /admin/sections/{id}` | Admin: draft section/meetings/capacity under invariants | FR-11 |
| `POST /admin/registration-windows`, `PATCH /admin/registration-windows/{id}` | Admin: configure, open/close windows with audit | FR-12 |

Administrative publication and update commands need a reviewed version/precondition strategy to prevent lost updates. The provisional freeze rule still applies. Exact publish/close operations and method semantics will be finalized with the domain lifecycle, not inferred from a generic `PATCH`.

## Mutation and replay contract

Enrollment and withdrawal require an `Idempotency-Key`; Phase 3 selects a client-generated random UUID as its format. Key scope, fingerprint, lock protocol, persistence and client recovery are defined in [Enrollment design](enrollment-design.md#idempotency-key-lifecycle). Future administrative commands need their own deduplication/version-precondition contract before implementation.

Proposed enrollment success is `201 Created` with the stable enrollment identifier, section/term, active state, and protected decision time `decidedAt`; withdrawal returns `200 OK` with withdrawn state. The timestamp is sampled inside the transaction and is not the exact database commit timestamp; success is emitted only after commit is acknowledged. Replaying a stored result returns the original terminal status/body during retention, subject to current authorization. If later withdrawn, replay of the original enrollment still describes the original operation; the client obtains current state from its enrollment/schedule read. A distinct-key duplicate active enrollment returns a typed conflict. Repeating an already completed withdrawal under a new key has no additional accounting effect and returns its existing withdrawn state if still authorized.

One key with a different target or canonical payload yields `IDEMPOTENCY_KEY_REUSED`. For an uncommitted same-key request, a bounded wait may return the committed result; otherwise return a retryable `REQUEST_IN_PROGRESS` outcome and advise reuse of that key. There is no independently committed processing lease. The [physical expiry/reuse protocol](database-concurrency.md#idempotency-claim-replay-and-expiry-algorithm) is specified; the supported retention duration remains TODO. Reuse after expiry can execute a new operation, so clients must not treat it as guaranteed replay. A stored business denial also replays unchanged; a deliberate reconsideration requires a new intention/key. Do not create a new key merely because an HTTP response timed out. After the supported retention horizon or loss of the key, reconcile authoritative own state rather than automatically submitting a new intention.

## Proposed outcome taxonomy

An error response includes a stable code, safe human-readable message, correlation identifier, and retryability indicator. Field-level validation details may be included without exposing academic records. Do not leak SQL, tokens, stack traces, or another student's enrollment details.

| Status | Example codes | Meaning for a valid benchmark |
| --- | --- | --- |
| `400` | `INVALID_REQUEST`, `IDEMPOTENCY_KEY_REQUIRED` | Invalid request; not an expected outcome in a valid workload |
| `401` | `AUTHENTICATION_REQUIRED` | Invalid/missing identity; benchmark setup or system issue for an authenticated scenario |
| `403` | `FORBIDDEN`, `COHORT_INELIGIBLE` | Access denial; only a deliberately generated, named cohort case is an expected business decision |
| `404` | `RESOURCE_NOT_FOUND` | Missing/not-visible resource; not expected for a valid fixture |
| `409` | `SECTION_FULL`, `ALREADY_ENROLLED`, `CREDIT_LIMIT_EXCEEDED`, `SCHEDULE_CONFLICT`, `PREREQUISITE_NOT_MET`, `REGISTRATION_CLOSED` | Typed business denial; expected only when the fixture predicts that code |
| `409` | `IDEMPOTENCY_KEY_REUSED`, `REQUEST_IN_PROGRESS`, `VERSION_CONFLICT` | Client conflict or retryable coordination outcome; classify separately, never blanket-ignore all `409` responses |
| `429` | `RATE_LIMITED` | Controlled rejection, reported separately and counted as unsuccessful target-workload service |
| `503` | `TEMPORARILY_UNAVAILABLE`, `ELIGIBILITY_DATA_UNAVAILABLE` | Retryable technical/unavailable outcome; no success confirmation |
| `500` and other unplanned responses | `INTERNAL_ERROR` or contract violation | Unexpected failure |

All database errors are mapped only after proper rollback. A commit acknowledgement failure is an ambiguous outcome, not proof of rollback; same-key replay resolves it. Timeout/retry headers, final status choices, admin concurrency fields, and a machine-readable OpenAPI contract are TODOs for a later phase.

## Contract verification planned

Later checks cover ownership/role isolation, typed business denials, same-key replay and fingerprint mismatch, post-commit lost responses, window boundaries, canonical timestamp serialization, bounded lists, and audit/correlation. Performance classification follows [Performance](performance.md); successful HTTP transport alone does not prove valid enrollment.

## Phase 5 implemented contract

The following surface is implemented; the broader table above remains the future product surface. Authentication is synthetic only and requires an explicit dev/test profile. No administrative CRUD/publication/window API is implemented in this phase.

| Method and full path | Input and authorization | Output |
| --- | --- | --- |
| GET /api/v1/auth/csrf | Anonymous or authenticated browser, same origin | 200 token/headerName/parameterName; retain HttpOnly SESSION cookie |
| POST /api/v1/auth/login | URL-encoded username/password; valid CSRF header and pre-auth cookie | 200 principalId/studentId/role, rotated session; generic 401 failure |
| POST /api/v1/auth/logout | Valid CSRF; invalidate the current session when present | 200 LOGGED_OUT, invalidate session |
| GET /api/v1/me or /api/v1/auth/me | STUDENT or ADMIN | 200 principalId/studentId/role |
| POST /api/v1/sections/{id}/enrollments | STUDENT, CSRF, UUID Idempotency-Key; target is the path ID, no JSON body required | 201 ENROLLED or typed denial |
| POST /api/v1/me/enrollments | Compatible alias with JSON sectionId; same STUDENT/CSRF/key requirements | Same canonical operation/fingerprint/replay as path-based endpoint |
| DELETE /api/v1/enrollments/{id} or /api/v1/me/enrollments/{id} | STUDENT, CSRF, UUID Idempotency-Key; ownership-filtered target | 200 WITHDRAWN or ALREADY_WITHDRAWN; unknown/not-owned 404 |
| GET /api/v1/me/enrollments?termId=UUID | STUDENT, own history; limit 1..100 default 100, offset 0..100000 default 0 | Array enrollmentId/sectionId/offeringId/termId/status/enrolledAt/withdrawnAt |
| GET /api/v1/me/schedule?termId=UUID | STUDENT; one read-only REPEATABLE READ snapshot | termId/timezone/observedAt/credits/active enrollments/meetings |
| GET /api/v1/courses/{id}/sections?termId=UUID | STUDENT; published offering/sections; same limit/offset bounds | sections/meetings/observedAt/cache=false; availability is advisory |

Fetch CSRF again after login/logout; unsafe requests and replays require it. Caller-supplied studentId/role/body or X-Student-ID/X-Role headers never establish identity. The aliases normalize to the same version-1 ENROLL(section UUID) or WITHDRAW(episode UUID) SHA-256 command. Unknown extra legacy JSON identity fields are ignored, never used for authority.

Mutation bodies contain code, decidedAt (UTC protected decision instant), correlationId and retryable=false; business-denial bodies also contain a safe message; successful bodies additionally contain enrollmentId, sectionId, termId, status and withdrawnAt (nullable). They are serialized to JSONB before commit and returned in that stored representation, so replay returns exactly the original status/body. The new HTTP X-Correlation-ID is independent of the original body correlationId. Idempotency-Replayed is true/false. Repeated already-withdrawn calls use code ALREADY_WITHDRAWN and do not append a second WITHDRAWN state-change audit; the new completed intention has its own ALREADY_WITHDRAWN audit.

Academic outcomes include 409 ACADEMIC_INELIGIBLE/PREREQUISITE_NOT_MET/CREDIT_LIMIT_EXCEEDED/SCHEDULE_CONFLICT/ALREADY_ENROLLED/SECTION_FULL/REGISTRATION_CLOSED and 403 COHORT_INELIGIBLE. All are durable business results. Missing/unavailable evidence is 503 ELIGIBILITY_DATA_UNAVAILABLE and rolls back the claim. Missing preprovisioned coordination/policy is unavailable, never unlocked success. Published evidence is pinned/frozen; no evidence-age TTL is inferred from captured_at because Q-05 has no approved freshness duration.

Conflicting Idempotency-Key reuse returns 409 IDEMPOTENCY_KEY_REUSED. A bounded same-key wait timeout returns 409 REQUEST_IN_PROGRESS, retryable=true, Retry-After: 1. Lock/statement/pool failures return 503 TEMPORARILY_UNAVAILABLE after rollback. Local admission exhaustion is 503 SERVICE_OVERLOADED. Login work limits are 429 RATE_LIMITED. Unexpected integrity/code errors return 500 INTERNAL_ERROR; ambiguous connection/acknowledgement failures may return 503 OUTCOME_UNKNOWN. No success is inferred from an exception. Recovery uses the same key, never an automatically generated replacement.

The isolated default retention is 24 hours from decidedAt; clients are supported for recovery within 23 hours of their initial submission. After expiry a deliberate reuse can execute a new operation/UUID. Do not automatically retry beyond that horizon: read authoritative own state and reconcile intent. The numeric retention/budgets are MVP configuration, not a shared deployment SLO. A formal OpenAPI document, catalog administration and deployment-wide limits remain future work.


## Student Portal read extensions

| Method and full path | Input and authorization | Output |
| --- | --- | --- |
| GET /api/v1/terms | STUDENT | Published terms ordered newest first, with `termId`, code, dates, timezone and the caller’s effective `creditLimit` if assigned |
| GET /api/v1/courses | STUDENT; required `termId`; optional literal case-insensitive `q` (up to 100 characters), `available` (default false), `limit` (1–100, default 24), `offset` (0–100000, default 0) | `items` with course/offering IDs, code, title and credits, plus `hasMore`. `available=true` requires a published section with a seat at observation time |
| GET /api/v1/courses/{id}/sections | STUDENT; optional `includeInstructors=true` | Existing snapshot plus `sectionDetails` with section ID, section code and nullable instructor name. Multiple instructors may be assigned |
| GET /api/v1/me/schedule | STUDENT; optional `includeCourseTitles=true` | Existing snapshot plus `courseTitles`, keyed by offering ID from the caller’s active enrollments |
| POST /api/v1/admin/benchmark-authorize | ADMIN session and CSRF | 200 authorization proof. Modal operations are executed by the separately enabled local Next.js operator server |

The browser calls the same-origin Next.js proxy at `/api/student/*`. The server forwards cookies, CSRF and idempotency headers to the configured Spring origin. The Student Portal preserves one pending mutation per principal and retries it with the original key during the documented recovery window. Seat availability and the credit meter are display values, and Spring validates the write against authoritative state.

If the application cannot acquire a transaction because the pinned JDBC stack confirms a transient connection failure or pool exhaustion, the HTTP advice returns `503 TEMPORARILY_UNAVAILABLE` with `Retry-After: 1`. Permanent acquisition failures retain the internal-error response.
