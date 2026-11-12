# Phase 4 implementation and executed verification

Status: **Backend Foundation & Database Verification implemented**, November 6, 2026; stop for review. This report distinguishes executed checks from future business/load acceptance. Only synthetic data and local Docker Linux containers were used; nothing was pushed to GitHub.

## Implemented scope

Kotlin/Spring Boot foundation with Gradle Kotlin DSL/wrapper/locks, Java 21 target, environment configuration, PostgreSQL/HikariCP, Flyway, Spring Security, JDBC sessions, Actuator health/readiness/liveness, correlation IDs, JSON ECS logs and safe problem responses. Public module boundaries are reserved; no domain entities/repositories/Enrollment service or student/course/enrollment business API are implemented.

The runnable local Compose environment contains PostgreSQL only, bound to loopback with a named data volume and healthcheck. Redis, Kafka, Kubernetes, frontend and paid services are absent. Hibernate is not a dependency; Hibernate, Spring SQL and Spring Session schema initialization are disabled. Flyway is the sole source of application schemas.

## Findings recorded before corrections

| ID | Observation | Correction / evidence |
| --- | --- | --- |
| F4-01 | On real PostgreSQL 18.6, `SELECT 'NaN'::numeric >= 0` returned true. Phase 3's nonnegative credit/grade checks therefore permitted NaN despite intending finite academic values. | Kept applied V1–V5 unchanged, added V6 finite checks on five numeric fields, synchronized the dictionary, and passed all five rejection cases. No table/relationship/academic-limit/identifier change. |

No ERD/dictionary field or FK disagreement was found. The nullable withdrawal-window composite FK intentionally uses MATCH SIMPLE; the status CHECK separately requires the withdrawal fields for WITHDRAWN. Session metadata follows the standard JDBC schema rather than an incompatible custom table.

## Flyway evidence

| Version | Executed responsibility |
| --- | --- |
| V1 | Eight schemas, retained NOLOGIN owner/support roles, restricted public/database access |
| V2 | 22 tables, 22 PKs, 21 ordinary unique keys, 28 FKs and dictionary CHECK constraints |
| V3 | Seven additional mandatory indexes: two active unique indexes, enrollment history, audit history, outcome expiry and two session lookup/expiry indexes |
| V4 | One-way episode update, immutable claim/completion and deferred terminal outcome guards |
| V5 | Runtime column grants, two guarded expiry helpers, two redacted observer views |
| V6 | Explicit NaN rejection for policy limit, offering credits, prerequisite grade, completed grade and student override |

All six applied on fresh Testcontainers databases. The local Compose database first applied V1–V5 from empty, then V6 as an additive correction. Subsequent migrate/validate runs applied zero new migrations and preserved checksums. The local backend startup also validated that schema successfully.

MigrationIntegrationTest reads the actual English dictionary, not a copied expected DDL. It compares table/column sets, PostgreSQL types/lengths/precision/scale, nullability/defaults, PK and unique names/columns, FK names/ordered columns/parents/actions/MATCH behavior, CHECK counts, index names/key order/predicates, and deferred-trigger metadata. Actual catalog: **22 application tables, 28 FKs, 50 mandatory indexes**. The optional catalog partial read index is absent. Flyway history/system tables and the two observer views are counted separately. Named CHECK source and focused invalid-state tests supplement the catalog comparison.

## Executed test results

**37 tests passed, 0 failed, 0 errors, 0 skipped**: 36 real-PostgreSQL integration scenarios plus one source-boundary check. Tests use JUnit Jupiter 5.14.4 and programmatic Testcontainers PostgreSQL 2.0.5, without H2 or Spring's JUnit 6 extension. Each database scenario has a fresh database and final counter/active-uniqueness/credit/terminal-state inspection before disposal. Smaller internal multi-case scenarios also inspect state where appropriate.

| Suite | Passing tests | Verified behavior |
| --- | --- | --- |
| MigrationIntegrationTest | 2 | Empty creation, validation/reapply, real catalog versus dictionary and stable/partial FK targets |
| ConstraintIntegrationTest | 10 | Capacity/count bounds, active section/offering uniqueness, one-way episodes, re-enrollment, composite term/offering/evidence/cohort integrity, nullable withdrawal shape, finite academic values, runtime/observer permissions |
| IdempotencyIntegrationTest | 8 | Deferred unfinished-claim rejection, final-row completion semantics, immutable outcomes, unique scope, typed NULL collection target, transaction rollback including audit/result, guarded cleanup and retained audit |
| LockingIntegrationTest | 12 | Compatible shared gates, exclusive close wait, student mutex, real FK key waits, no early target FK at claim, final-seat rechecking, same-key commit/rollback contenders, cleanup/replacement races, late academic reads/capacity, fresh own credits/meetings, post-wait wall time and controlled deadlock recovery |
| HttpFoundationIntegrationTest | 4 | Runtime datasource identity, low-detail health, inaccessible business/management routes, CSRF denial, correlation/error redaction, real Spring Session save/find/rotation/delete, readiness loss/recovery with healthy liveness |
| ModuleBoundaryTest | 1 | Production source does not import another module's internal package |

The intentionally unsafe gate-upgrade test observes one SQLSTATE 40P01 victim and one completing transaction, with rollback and valid final state. That is a fault-injection check, not the recommended protocol. Concurrent tests use observable PostgreSQL blocking state and bounded waits instead of assuming thread scheduling.

## Packaged backend and environment

The executable Boot jar was built and started against the Compose PostgreSQL database. Flyway validated all six migrations; HikariCP connected as `portal_runtime`. HTTP health, liveness and readiness returned `200 {"status":"UP"}`. Actual console output is JSON ECS. No default login password, test-only probe route or business API is exposed by the production artifact.

Verification host: macOS arm64, Docker Engine 29.1.2 / Desktop 4.54.0, Compose 2.40.3; server PostgreSQL 18.6. Java toolchain target is 21. The initial full suite passed on installed Temurin 21.0.12.1+1; the packaged smoke used Temurin 21.0.13+9. The final full build and all 37 tests also passed on Temurin 21.0.13+9. A subsequent ordinary locked build succeeded without rewriting locks; commands are recorded below. Windows 11 commands are documented but not executed here. [Versions](technology-versions.md) records official compatibility sources and exact resolved libraries.

The temporary packaged application is stopped after its smoke check; the Compose PostgreSQL service remains available for review. Testcontainers databases/container are disposed at worker shutdown. The local `.env` has generated passwords, is ignored by Git and has restricted file permissions. No plaintext local credential was added to source/migrations/log output.

## Reproduction commands

From the root, configure/load environment as documented in [README](../README.md), then:

```sh
docker compose --env-file .env -f infrastructure/compose.yaml up -d --wait postgres
cd backend
./gradlew migrate
./gradlew build
./gradlew bootRun
```

A user-level `org.gradle.java.home` on the host initially selected a different JDK 21 patch despite JAVA_HOME. To verify a specific installed JDK without editing global settings:

```sh
./gradlew "-Dorg.gradle.java.home=$JAVA_HOME" build
```

Test XML is under `backend/build/test-results/test/`; HTML is `backend/build/reports/tests/test/index.html`. These generated files are ignored and regenerated by the commands. Tests deliberately fail if Docker is unavailable rather than silently skipping mandatory verification.

## Late-section-lock proposal and remaining risks

AD-13 proposes preparing stable academic facts under gate S and student U, then acquiring section U for final mutable/time-sensitive checks and allocation. The two-connection cases verify relevant visibility/lock/time properties. No production Enrollment path exists, and no optimization has been deployed or benchmarked. Phase 5 must implement the reviewed full protocol and repeat complete academic/transaction/API acceptance before adopting it.

Still unimplemented/unverified: positive synthetic login and full student/admin ownership flows; complete Enrollment/Withdrawal service, semantic idempotency fingerprint/replay HTTP behavior, retry/deadline orchestration, full prerequisite/credit/schedule/window acceptance, publication freeze through business APIs, process-crash/lost-response recovery, 2,000 contenders/30 seats, and 5,000 VUs. No latency/throughput/capacity conclusion follows from the small suite. RPO/RTO, deployment hardening, production identity, retention/budget values and repository ownership remain open where previously recorded.

The local migration login has cluster administration rights for initial role provisioning, while runtime does not. Shared deployments should separate migration/credential provisioning from runtime and review statement logging/session serialization before exposing authentication. Schema privileges do not isolate code modules sharing a trusted service role; source boundaries and future owning APIs remain necessary. Counter/ledger equality still depends on the approved transactional protocol; no cross-table CHECK/aggregate repair trigger is claimed.

## Phase 5 handoff

Stop for review. On separate instruction, implement synthetic identity and full enrollment/withdrawal orchestration, approve AD-13's final ordering, define exact API/idempotency/error contracts and deadlines, enforce all stable academic checks, and add complete service/API/failure acceptance. Keep one root transaction/connection, PostgreSQL authority, actor ownership, section bounds and active uniqueness. Frontend, real SDU data, large load tests and GitHub publication remain outside this completed phase.
