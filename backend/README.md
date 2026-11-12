# Backend core enrollment

Kotlin/Spring Boot modular monolith, Gradle Kotlin DSL and Java 21. Phase 5 implements synthetic STUDENT/ADMIN authentication, durable idempotent enrollment/withdrawal, academic checks and coherent own-schedule reads. Phase 6 adds bounded-label instrumentation, a separately protected Prometheus listener in the benchmark profile, and an opt-in early-lock comparison. The small HTTP baseline is measured; large-load capacity is unproven.

Use the root [README](../README.md) for environment loading and Compose. `./gradlew build` runs the regular PostgreSQL/HTTP suite and packages the executable jar. `./gradlew correctnessStress` explicitly runs the separate 2,000-student/30-seat service correctness fixture; it is excluded from the regular suite, not reported skipped. Tests need Docker and no local `.env`. `-PstressWorkers=4` reduces service-test concurrency on a constrained host without changing its 2,000 attempts.

All schema creation comes from `src/main/resources/db/migration/`. V1–V7 are unchanged in Phase 6; V7 is the narrow student-term locking capability. SQL/session/Hibernate automatic DDL is disabled. Runtime and migration identities remain separate.

Public `api` contracts connect module adapters. EnrollmentService owns bounded admission/retries outside transactions; the separately proxied EnrollmentAttempt owns READ COMMITTED. Joined adapters require MANDATORY propagation on Spring's single domain transaction manager and connection. The runtime role cannot update academic student-term assignments.

`./gradlew syntheticAccount` is an offline operator task requiring dev/test, SYNTHETIC_LOGIN, SYNTHETIC_ROLE, optional SYNTHETIC_STUDENT_ID and SYNTHETIC_PASSWORD supplied outside Git. No default password is shipped. See the [runbook and executed verification](../docs/phase5-verification.md) and [HTTP contract](../docs/api-specification.md#phase-5-implemented-contract).

[Versions](../docs/technology-versions.md), [dictionary](../docs/database.md), [lock protocol](../docs/database-concurrency.md), [security](../docs/security.md) and [AD-13](../docs/architecture-decisions.md#ad-13--prepare-immutable-academic-facts-before-the-hot-section-lock) document the boundaries. The [Backend repository](https://github.com/Ruslando123/SDU_Portal_Backend) owns this module, infrastructure and shared documentation; the Next.js app is a pinned frontend submodule. See the [repository workflow](../docs/repository-layout.md).

See the [Phase 6 performance report](../docs/phase6-performance-report.md) and [isolated monitoring setup](../infrastructure/monitoring/README.md).
