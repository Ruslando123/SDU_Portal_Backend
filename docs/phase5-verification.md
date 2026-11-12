# Phase 5 implementation and executed verification

Verified on November 6, 2026. Phase 5 is ready for review. Only synthetic data/accounts were used. Phase 6, frontend, Redis, brokers, paid APIs, real SDU accounts and GitHub publication were not performed. No 5,000-user capacity is asserted.

## Executed results

`build correctnessStress` completed successfully using the locked dependencies and actual PostgreSQL. The regular suite passed **81 tests, zero failures, errors or skips**. The separately invoked correctness suite passed **1 test, zero failures, errors or skips**: **82 passing tests across the two tasks**. The allocation test is excluded by tag from ordinary `test`, not silently skipped. All 37 original foundation cases remain; MigrationIntegrationTest now expects seven migrations instead of six.

| Regular class | Passed | Evidence |
| --- | ---: | --- |
| ConstraintIntegrationTest | 10 | Original constraints, finite values, retained episodes and least privilege |
| IdempotencyIntegrationTest | 8 | Original physical guards, rollback and expiry primitives |
| LockingIntegrationTest | 12 | Original two-connection FK/locks, actual PostgreSQL deadlock and AD-13 primitives |
| MigrationIntegrationTest | 2 | Fresh migration, validation, exact 22-table/28-FK/50-index dictionary |
| HttpFoundationIntegrationTest | 4 | Original health/readiness, safe errors, correlation and JDBC session adapter |
| ModuleBoundaryTest | 1 | No foreign internal imports, forbidden module edges or prohibited command/allocation callers |
| EnrollmentImplementationTest | 30 | Complete core service cases, concurrency, retry/failure injection and metrics |
| AuthenticationImplementationTest | 10 | HTTP login/logout/CSRF/ownership/revocation, aliases, own reads and another backend |
| AuthenticationDefaultsTest | 1 | Actual default Secure/HttpOnly/SameSite cookie |
| Phase5MigrationUpgradeTest | 1 | Populated V6 → V7, historical checksums/data retained, no academic UPDATE grant |
| EnrollmentAdmissionTest | 1 | Excess admitted work rejected before another root transaction |
| EnrollmentDeadlineTest | 1 | Total deadline bounds fresh serialization retries |
| **Regular total** | **81** | **Zero failures/errors/skips** |

Generated evidence is under `backend/build/reports/tests/test/`, `backend/build/test-results/test/`, `backend/build/reports/tests/correctnessStress/`, `backend/build/test-results/correctnessStress/` and `backend/build/reports/phase5-correctness-stress.txt`. Build output is ignored; this report records the observed values.

## Environment

| Component | Observed |
| --- | --- |
| Host | macOS arm64; timezone Asia/Almaty |
| JVM | Eclipse Temurin 21.0.13+9, JVM/bytecode target 21 |
| Kotlin / Gradle / Boot | 2.4.21 / wrapper 9.7.0 / Spring Boot 4.1.2 |
| Security / Session JDBC | 7.1.2 / 4.1.1 |
| PostgreSQL | 18.6, official Alpine image pinned below |
| Flyway / HikariCP / JDBC | 12.4.0 / 7.0.2 / 42.7.14 |
| JUnit / Testcontainers | Jupiter 5.14.4, Platform 1.14.4 / PostgreSQL 2.0.5 |
| Docker | Engine 29.1.2, Desktop 4.54.0, Compose 2.40.3 |
| Test isolation | Programmatic Testcontainers; isolated DBs per primitive case/application class; serial classes |
| Local Compose | Loopback 127.0.0.1:15432, database sdu_registration, migrated V6 → V7 |

Image: `postgres:18.6-alpine@sha256:123b17998ccaf47e53b502e8c6a1e79afd8f579c6f3eec83c563da026f957395`. Dependencies and wrapper versions remain unchanged; no additional runtime dependency was needed. Windows commands are documented, not executed. See [versions](technology-versions.md).

## Implemented authentication and API

Authentication uses predefined PostgreSQL test_accounts with a delegating password encoder (offline provisioning defaults to BCrypt). Login is a Spring Security form authentication flow, with session fixation rotation and credentials erased before security-context serialization. A serializable minimal principal carries principal/student/role/auth_version; its hash is transient. Spring Session persists to the existing Flyway-owned PostgreSQL tables. An HTTP test inspected stored session bytes and found neither plaintext secret nor verifier.

Each protected API request checks current enabled/auth_version/role/student mapping before authorization/replay. Logout invalidates the session; disabling/version changes revoke future access. Already admitted transactions can finish. Default cookies are host scoped, Secure, HttpOnly, SameSite=Lax, with cookie-only tracking. Local dev explicitly disables Secure for loopback HTTP. Idle lifetime is 30 minutes and authenticated absolute lifetime 8 hours. Login verification is bounded locally to 8 concurrent verifications and 60 attempts per minute; these are MVP controls, not deployment-wide abuse protection. The backend refuses startup without an explicit dev/test profile.

Implemented endpoints:

- `GET /api/v1/auth/csrf`; `POST /api/v1/auth/login`; `POST /api/v1/auth/logout`.
- `GET /api/v1/me` and `/api/v1/auth/me` for STUDENT/ADMIN identity.
- `POST /api/v1/sections/{id}/enrollments`; compatible `POST /api/v1/me/enrollments` JSON sectionId alias.
- `DELETE /api/v1/enrollments/{id}` and `/api/v1/me/enrollments/{id}` alias.
- `GET /api/v1/me/enrollments?termId=UUID` with bounded limit/offset.
- `GET /api/v1/me/schedule?termId=UUID` in one read-only REPEATABLE READ snapshot.
- `GET /api/v1/courses/{id}/sections?termId=UUID` for published offering/section snapshots.

CSRF protects login, logout and all unsafe requests, including replay. Fetch a new token after login/logout. Headers/body cannot supply trusted student/role identity. ADMIN login works but student commands/schedule return 403. Unknown/not-owned withdrawal returns the same 404 body shape with no foreign IDs. Full contracts and error taxonomy are in [API specification](api-specification.md#phase-5-implemented-contract).

## Actual root transaction and AD-13

EnrollmentService derives the student from Authentication, validates the UUID key, rejects an ambient transaction and acquires a bounded permit before calling a different Spring bean, EnrollmentAttempt. Its actual Spring transaction interceptor starts READ COMMITTED and commits before the orchestrator receives the return value. Joined module adapters use MANDATORY propagation and JdbcTemplate's Spring-bound connection. There is one domain transaction manager, no self-invoked transaction entry, independent module commit, REQUIRES_NEW, manual domain connection or external service call in the path.

The runtime sequence is claim/replay → term gate FOR SHARE → student-term FOR UPDATE → immutable evidence/offering/AND prerequisites/current ACTIVE memberships/batched credits/meetings → late section FOR UPDATE → recheck section identity/publication and PostgreSQL clock_timestamp/window → conditional counter increment → new ACTIVE episode → transactional audit → terminal stored response → one root commit → HTTP. A test verifies an actual AOP proxy, a single transaction-manager bean and identical PostgreSQL xmin for the section, episode, audit and outcome writes.

Student U serializes credit/meeting/duplicate decisions across all sections of one student/term. Independent students only serialize on a common section. Published facts and assignments stay frozen under the gate protocol. No evidence freshness TTL is invented from captured_at: Q-05 remains unresolved. Window boundaries are half-open and evaluated after section waits; duplicate findings take precedence among academic findings after current window/publication validation. Required occurrences use strict half-open overlap. BigDecimal sums use published credits and pinned override/default limit, with no floating point or stored student credit counter.

Withdrawal claims its own operation, locates only an owned episode, takes gate/student/section protection, re-reads state, checks its WITHDRAW window at fresh wall time and conditionally changes ACTIVE → WITHDRAWN and decrements once. New-key repeats return ALREADY_WITHDRAWN without another decrement or WITHDRAWN state-change event. Re-enrollment creates a new UUID; retained history and old enrollment replay remain historical.

## F5-01 and additive migration

The first runtime-role service test exposed PostgreSQL permission denial on raw student_terms SELECT FOR UPDATE: V5 had SELECT/INSERT but intentionally no academic UPDATE permission. Primitive Phase 4 lock tests used the fixture connection and had not exercised this runtime-role capability.

V1–V6 were preserved byte for byte (all six original SHA-256 values compared to the pre-Phase-5 snapshot). V7 adds only `enrollment.lock_student_term(uuid,uuid)`: a VOLATILE SECURITY DEFINER function owned by portal_owner, fixed pg_catalog search_path, PUBLIC execution revoked, runtime execution granted. It takes the existing row FOR UPDATE in the caller's transaction and returns the pinned policy/evidence/credit override. No table, column, FK, index or broad UPDATE permission was added. The separate populated-V6 upgrade test verifies original Flyway checksums/data, one new migration and runtime locking without academic update rights.

This follows the observed behavior and [PostgreSQL SELECT permissions](https://www.postgresql.org/docs/18/sql-select.html) and [SECURITY DEFINER guidance](https://www.postgresql.org/docs/18/sql-createfunction.html). The local Compose database also upgraded from 6 to 7 and validated successfully.

## Durable idempotency, failures and bounds

Scope is the immutable authenticated principal + ENROLL/WITHDRAW + version 1 + UUID key. SHA-256 canonical command fingerprints normalize aliases and include typed target UUID, excluding credentials, CSRF, correlation and forged identity fields. INSERT ON CONFLICT DO NOTHING is followed by a separate SELECT FOR UPDATE on conflict. There is no separately committed lease, map or cache authority. Identical contenders wait/replay within bounds; mismatched retained commands return IDEMPOTENCY_KEY_REUSED.

Stable business results (including full, duplicate, closed, academic and ownership denials) commit audit plus completed outcome. Missing evidence/configuration, timeout, dependency failures and unexpected SQL errors roll back all effects and the claim. Only 40P01/40001 receive fresh-transaction bounded retries with jitter. Trigger-injected PostgreSQL SQLSTATE cases verify success after two aborted attempts and finite exhaustion; the retained primitive suite also creates a real PostgreSQL deadlock. A deferred trigger failure at commit cannot produce success. Unique/integrity failures are rolled back and reported as technical faults, not invented SECTION_FULL or success.

Default local bounds: 64 admitted mutations, 3 attempts, 15-second total retry deadline, 10-second root timeout, 2-second lock timeout, 5-second statement timeout capped by remaining deadline, pool acquisition 3 seconds (test profile 1 second). Same-key wait expiration is REQUEST_IN_PROGRESS; ordinary lock/pool timeout is TEMPORARILY_UNAVAILABLE; admission exhaustion is SERVICE_OVERLOADED. Connection/commit ambiguity is conservatively OUTCOME_UNKNOWN and recovered using the same key. No automatic new-key retry exists. A slow commit acknowledgement is not cancellable merely because a client deadline elapsed.

Terminal JSON is stored/returned in PostgreSQL JSONB's representation; replay returns the exact original status/body, even after withdrawal. Retention is 24 hours from protected decision time; the isolated supported recovery horizon is 23 hours from original submission. On expiry the locked old operation UUID is deleted through the existing guarded helper and a new claim is inserted in the same transaction. No audit/episode history points to the expiring result. The upgrade/expiry/replay tests use real PostgreSQL and authoritative queries.

## Coverage of the requested scenarios

| Requested cases | Executed coverage |
| --- | --- |
| 1–5 success/full/duplicate/replay/mismatch | EnrollmentImplementationTest; concurrent identical requests, same-key scopes and both HTTP aliases |
| 6–10 credit/clash/prerequisite/evidence/window | Exact override arithmetic, half-open required meetings, matching prerequisite scheme/grade, unavailable evidence rollback, closed/natural closing |
| 11–13 withdraw/double/re-enroll | Release once, retained history, new episode and historical enrollment replay; independent withdrawal window |
| 14–16 student credit/clash races and last seat | Two-thread PostgreSQL races plus separate application instances for student credit protection |
| 17–18 partial change and pre-commit failure | Real insert-trigger failure after increment and deferred audit-trigger failure during commit; no partial ledger/counter/audit/outcome |
| 19–21 lost response/historical replay/expiry | Discard a committed service response, same-key replay, replay after withdrawal and old-UUID expiry/reuse |
| 22–23 withdrawal/enrollment and admin close | Concurrent service calls; controlled exclusive-gate admin close; post-section-wait natural closing |
| 24–26 ownership/CSRF/revocation | Real HTTP including forged identity fields/headers, disabled/version-revoked sessions, ADMIN restrictions and session rotation/logout |
| 27 bounded transient handling | Real PostgreSQL SQLSTATE injection for 40P01/40001, finite attempts/deadline, physical deadlock primitive, section/claim/pool timeout and admission rejection |

The lost-response case deliberately discards an acknowledged committed service result; it does not simulate TCP acknowledgement loss. The failure-before-commit cases inject real SQL/commit failures, not an OS process kill. These evidence limits are explicit.

## Correctness allocation: 2,000 distinct students and 30 seats

The explicit `correctnessStress` task builds an empty published 30-seat section, 2,000 distinct STUDENT principals/student-term/evidence records, COMPLETE/ACTIVE evidence, open windows, allowed policy and no prerequisites/clashes. All 2,000 intentions are submitted to a bounded executor, 32 worker calls compete with a runtime pool of 16. The initial worker wave is released through a latch. This is a service-boundary scenario through the real Authentication principal resolver, EnrollmentCommands, Spring proxies and runtime JDBC role; it excludes HTTP/login/session persistence overhead and does not mean 2,000 simultaneous database connections or HTTP requests.

Observed recorded run:

| Result | Actual |
| --- | ---: |
| Distinct eligible students / requested intentions | 2,000 / 2,000 |
| ENROLLED | 30 |
| SECTION_FULL | 1,970 |
| Technical/other outcomes | 0 |
| Distinct ACTIVE students / section count | 30 / 30 |
| Completed outcomes | 2,000 |
| Enrollment state-change audit events | 30 |
| Checked invariant violations | 0 |
| Workload elapsed, excluding fixture/startup | 4,536 ms |

Elapsed time is an observation of this correctness run, not a published latency/throughput SLO. The test fails for any technical outcome, insufficient allocation, wrong business count or invariant violation. The test fixture is dropped after execution.

## Independent SQL checks

`backend/verification/phase5-invariants.sql` runs a read-only REPEATABLE READ post-workload snapshot. It independently checks section bounds/count-versus-ACTIVE ledger, active student/section and student/offering uniqueness, exact pinned credit sums, required meeting intersections, completed result/body coherence and retained enrollment/withdrawal audit presence. TestDatabase.invariants and Phase5InvariantChecks execute equivalent assertions after the new application/concurrency cases and allocation fixture. All checked counts were zero.

The same script was separately executed against the local Compose database after a packaged-jar HTTP workflow: all eight violation counts were zero, with one ENROLLED and one WITHDRAWN retained outcome. Counts alone do not prove every hypothetical writer or future policy; FK/CHECK/unique enforcement and the reviewed writer order remain part of the argument.

## Metrics

Micrometer records enrollment.attempts, enrollment.successes (ENROLL/WITHDRAW), enrollment.denials by bounded business code, enrollment.technical by bounded technical code, enrollment.replays, enrollment.retries by 40P01/40001, enrollment.transaction and enrollment.section.lock.wait. Hikari's actual hikaricp.connections.acquire timer measures database pool acquisition, including sessions/authentication. The registry/labels were asserted in integration tests. Timers around the proxied root include connection acquisition/commit/rollback; retry backoff is excluded. Successes include completed new-key 2xx outcomes; replay has its own counter.

Metrics are best effort and cannot fail a transaction; durable audit remains transactional. No principal, student, session, key, transcript or raw URL label is used. No credential, cookie or CSRF token is logged. This phase uses the in-process Micrometer registry and keeps management endpoints closed except health; a Prometheus/Grafana deployment/exporter is not claimed installed.

## Reproduction

With Docker running and JAVA_HOME selecting JDK 21, from backend:

```sh
./gradlew "-Dorg.gradle.java.home=$JAVA_HOME" build
./gradlew "-Dorg.gradle.java.home=$JAVA_HOME" correctnessStress
```

The second task always executes and is separate from the fast suite. No local `.env` is required for these tests; no H2 or hosted database is used. A Docker failure is a failed run, not a skip. A user-level org.gradle.java.home can override JAVA_HOME, so keep the explicit argument when selecting the verification JDK.

For local Compose, load the reviewed ignored `.env` as described in the root README, then run `./gradlew migrate`. Existing V6 installs apply one migration; a second validated run applies none. Do not edit V1–V6, clean Flyway or delete the volume to repeat verification.

## Local synthetic accounts and manual API use

The task prepared the optional fixture in `infrastructure/synthetic-demo.sql` on the previously empty local Compose database, then used `syntheticAccount` to provision STUDENT and ADMIN with independently generated passwords. The secrets exist only in ignored mode-0600 `.env.synthetic`, using DEMO_STUDENT_LOGIN/PASSWORD and DEMO_ADMIN_LOGIN/PASSWORD; no secret is printed here or committed. The fixture IDs are public synthetic identifiers, never credentials. The service is stopped after smoke verification; PostgreSQL remains running.

For a fresh isolated database, first apply migrations, then apply the fixture once from the root:

```sh
docker compose --env-file .env -f infrastructure/compose.yaml exec -T postgres sh -c 'exec psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1' < infrastructure/synthetic-demo.sql
```

Do not rerun the once-only fixture against an existing populated database. With DB credentials and explicit dev profile exported, create a student from backend using a unique operator-supplied secret outside Git:

```sh
export SYNTHETIC_LOGIN=synthetic.student
export SYNTHETIC_ROLE=STUDENT
export SYNTHETIC_STUDENT_ID=10000000-0000-4000-8000-000000000001
read -r -s SYNTHETIC_PASSWORD
export SYNTHETIC_PASSWORD
./gradlew syntheticAccount
unset SYNTHETIC_PASSWORD
```

Use 16–72 characters and at most 72 UTF-8 bytes. For ADMIN, set SYNTHETIC_ROLE=ADMIN, use another login and independent secret, and unset SYNTHETIC_STUDENT_ID. Existing logins/mappings are not overwritten by this CLI. The CLI is offline, uses operator migration credentials, and never becomes a public signup or account-switch endpoint.

For the already prepared local workspace, load reviewed `.env` and `.env.synthetic` without printing them, start `./gradlew bootRun`, and in another process with DEMO_* values exported run `python3 backend/tools/synthetic-smoke.py` from the root. This stdlib client retains a cookie jar, obtains/refreshes CSRF, logs in the student, enrolls the synthetic section, reads schedule, withdraws, replays the original response, logs out, verifies the revoked session, logs in ADMIN and verifies student-command denial. Credentials/tokens/cookies are not printed. It leaves a withdrawn historical episode; subsequent runs use new intention UUIDs. The packaged jar passed this exact workflow with 200 login/schedule/withdraw/logout, 201 enrollment/replay, 401 old session and 403 ADMIN student-command denial.

For independent local SQL verification:

```sh
docker compose --env-file .env -f infrastructure/compose.yaml exec -T postgres sh -c 'exec psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1' < backend/verification/phase5-invariants.sql
```

## Remaining limits and next review

Academic choices remain provisional project assumptions, including two-decimal credits, AND prerequisites, grade-scheme matching, bundled sections, one ACTIVE section per offering and no evidence-age rule. They are not official SDU policy. Admin publication/capacity/provisioning/window endpoints, broad catalog/search, real SSO, shared rate limiting, audit retention/backups, serializer hardening and production TLS/secret operations remain separate work. Controlled admin-close tests use the documented fixture/operator path, not an implemented admin HTTP API.

All writers must preserve the gate/frozen-facts protocol. The trusted runtime role and source module boundaries cannot protect against arbitrary malicious SQL with all runtime privileges; aggregate counter equality is maintained by the root protocol, not a new aggregate trigger. Boot still runs Flyway with separate operator credentials in the isolated local setup; a shared deployment should run migrations separately.

No live PostgreSQL failover, OS-process kill, TCP acknowledgement-loss injection, 2,000-request HTTP storm, 5,000 VUs or Windows benchmark was run. Connection/commit ambiguity is handled conservatively, but those deployment failure modes need dedicated validation. Local admission/login caps and deadlines are correctness-stage defaults, not measured cluster budgets. Metrics are instrumented but no monitoring stack is deployed.

Recommend Phase 6 only after reviewing this implementation: finalize workload/academic assumptions, collect query plans and lock/pool/HTTP timing, validate process/dependency failure recovery, then define and authorize controlled performance engineering. Do not infer capacity from the service fixture. No Phase 6 work starts automatically.

## Phase 5 file inventory

The repository has no baseline commit; this inventory compares the pre-Phase-5 workspace snapshot. No commit or push was made. Historical V1–V6, dependency lockfiles, wrapper, Compose configuration and frontend are unchanged. Local `.env.synthetic` is ignored and excluded below.

Modified existing files:

```text
README.md
backend/README.md
backend/build.gradle.kts
backend/src/main/kotlin/org/example/sdu/registration/academicrules/api/README.md
backend/src/main/kotlin/org/example/sdu/registration/authentication/api/README.md
backend/src/main/kotlin/org/example/sdu/registration/authentication/internal/web/SecurityConfiguration.kt
backend/src/main/kotlin/org/example/sdu/registration/catalog/api/README.md
backend/src/main/kotlin/org/example/sdu/registration/enrollment/api/README.md
backend/src/main/kotlin/org/example/sdu/registration/schedule/api/README.md
backend/src/main/kotlin/org/example/sdu/registration/sections/api/README.md
backend/src/main/kotlin/org/example/sdu/registration/shared/errors/GlobalExceptionHandler.kt
backend/src/main/kotlin/org/example/sdu/registration/shared/errors/ProblemResponses.kt
backend/src/main/kotlin/org/example/sdu/registration/students/api/README.md
backend/src/main/resources/application-dev.yml
backend/src/main/resources/application.yml
backend/src/test/kotlin/org/example/sdu/verification/MigrationIntegrationTest.kt
backend/src/test/kotlin/org/example/sdu/verification/ModuleBoundaryTest.kt
backend/src/test/kotlin/org/example/sdu/verification/PostgresSupport.kt
docs/api-specification.md
docs/architecture-decisions.md
docs/architecture.md
docs/data-integrity.md
docs/database-concurrency.md
docs/database.md
docs/enrollment-design.md
docs/performance.md
docs/requirements.md
docs/security.md
docs/technology-versions.md
```

Added files:

```text
backend/src/main/kotlin/org/example/sdu/registration/academicrules/api/AcademicPolicy.kt
backend/src/main/kotlin/org/example/sdu/registration/academicrules/internal/JdbcAcademicPolicy.kt
backend/src/main/kotlin/org/example/sdu/registration/authentication/api/CurrentActor.kt
backend/src/main/kotlin/org/example/sdu/registration/authentication/internal/Accounts.kt
backend/src/main/kotlin/org/example/sdu/registration/authentication/internal/provisioning/SyntheticAccountCli.kt
backend/src/main/kotlin/org/example/sdu/registration/authentication/internal/web/AuthenticationController.kt
backend/src/main/kotlin/org/example/sdu/registration/catalog/api/CatalogFacts.kt
backend/src/main/kotlin/org/example/sdu/registration/catalog/internal/JdbcCatalogFacts.kt
backend/src/main/kotlin/org/example/sdu/registration/enrollment/api/EnrollmentApi.kt
backend/src/main/kotlin/org/example/sdu/registration/enrollment/internal/EnrollmentAttempt.kt
backend/src/main/kotlin/org/example/sdu/registration/enrollment/internal/EnrollmentController.kt
backend/src/main/kotlin/org/example/sdu/registration/enrollment/internal/EnrollmentQueries.kt
backend/src/main/kotlin/org/example/sdu/registration/enrollment/internal/EnrollmentRepository.kt
backend/src/main/kotlin/org/example/sdu/registration/enrollment/internal/EnrollmentService.kt
backend/src/main/kotlin/org/example/sdu/registration/schedule/internal/ScheduleController.kt
backend/src/main/kotlin/org/example/sdu/registration/sections/api/SectionAccess.kt
backend/src/main/kotlin/org/example/sdu/registration/sections/internal/JdbcSectionAccess.kt
backend/src/main/kotlin/org/example/sdu/registration/sections/internal/SectionController.kt
backend/src/main/kotlin/org/example/sdu/registration/shared/database/Sql.kt
backend/src/main/kotlin/org/example/sdu/registration/shared/errors/ApiFailure.kt
backend/src/main/kotlin/org/example/sdu/registration/shared/idempotency/DurableOutcomes.kt
backend/src/main/kotlin/org/example/sdu/registration/shared/observability/EnrollmentMetrics.kt
backend/src/main/kotlin/org/example/sdu/registration/students/api/AcademicEvidence.kt
backend/src/main/kotlin/org/example/sdu/registration/students/internal/JdbcStudentEvidence.kt
backend/src/main/resources/db/migration/V7__student_term_lock_capability.sql
backend/src/test/kotlin/org/example/sdu/verification/AuthenticationDefaultsTest.kt
backend/src/test/kotlin/org/example/sdu/verification/AuthenticationImplementationTest.kt
backend/src/test/kotlin/org/example/sdu/verification/CorrectnessStressTest.kt
backend/src/test/kotlin/org/example/sdu/verification/EnrollmentAdmissionTest.kt
backend/src/test/kotlin/org/example/sdu/verification/EnrollmentDeadlineTest.kt
backend/src/test/kotlin/org/example/sdu/verification/EnrollmentImplementationTest.kt
backend/src/test/kotlin/org/example/sdu/verification/Phase5InvariantChecks.kt
backend/src/test/kotlin/org/example/sdu/verification/Phase5MigrationUpgradeTest.kt
backend/src/test/kotlin/org/example/sdu/verification/Phase5Support.kt
backend/tools/synthetic-smoke.py
backend/verification/phase5-invariants.sql
docs/phase5-verification.md
infrastructure/synthetic-demo.sql
```
