# High-Performance University Course Registration System

The SDU synthetic Student Portal and Performance Lab run locally with a Kotlin/Spring Boot backend and PostgreSQL. Cloud evidence is tracked separately from the original Phase 6 archive. See the [final demo guide](docs/final-demo-guide.md), [release verification](docs/final-verification.md), [capacity report](docs/performance-capacity-report.md) and [mixed workload report](docs/mixed-overload-admission.md).

## Clone the complete workspace

```sh
git clone --recurse-submodules https://github.com/Ruslando123/SDU_Portal_Backend.git SDU_Portal
cd SDU_Portal
```

This private repository owns `backend/`, `infrastructure/` and shared `docs/`. `frontend/` is a Git submodule pinned to the separate private [Frontend repository](https://github.com/Ruslando123/SDU_Portal_Frontend). Authenticate Git for both repositories. If you already cloned without submodules, run `git submodule update --init --recursive`. See the [repository workflow](docs/repository-layout.md) for frontend editing and updates.

## Project overview and problem statement

Build an alternative course registration portal with explicit academic rules, transactional consistency and measurable performance. The brief describes a possible registration-opening workload of approximately 4,000–5,000 active students. This is a design input, not a verified finding about SDU's production portal or an achieved capacity result.

The working name is `sdu-course-registration`; this existing Git workspace is the project root regardless of its local folder name. Code/docs are in English. Only synthetic test data is used; there is no university-account integration.

## Project goals

Preserve section capacity, active uniqueness, credit limits, prerequisites and student schedule correctness under concurrency. Begin with a modular monolith and verify database mechanisms before implementing business APIs. The 5,000-active-student target remains an unverified benchmark hypothesis.

## Current project status

The Kotlin backend compiles and an executable Boot jar starts against local PostgreSQL. Flyway V1–V7 create/validate the proposed 22 tables, 28 FKs, 50 mandatory indexes, lifecycle guards, restricted cleanup helpers, two observer views and database roles. V8 adds an optional ordered RAM/RabbitMQ projection function; V1–V7 remain unchanged. The optional catalog read index is deliberately absent. Hibernate/JPA is not a dependency, Hibernate schema generation is disabled, and Spring Session/SQL automatic initialization is disabled.

The [Phase 6 report](docs/phase6-performance-report.md) records the original controlled 10-VU mixed HTTP baseline, isolated reliability tests, and small hotspot comparisons. The [Phase 7A report](docs/phase7a-reliability-verification.md) records the later database session reliability checks. The [current verification report](docs/autonomous-verification.md) covers the Student Portal, recent regression checks, and measured Modal runs.

The Student Portal supports synthetic sign-in and sign-out, course search, published section and instructor selection, enrollment, withdrawal and personal schedules. Performance Lab serves the Phase 6 archive and saved Modal runs, with optional private live telemetry and administrator controls. The system uses synthetic accounts and fixtures.

## Architecture overview

`Next.js → Kotlin/Spring Boot modular monolith → PostgreSQL`

The core module boundaries are Authentication, Student Management, Course Catalog, Course Sections, Enrollment, Academic Rules, Student Schedule and Administration. A read-only Student Portal projection supplies published catalog and student-term summaries. HikariCP bounds runtime connections. PostgreSQL owns confirmed academic state. JDBC is the default HTTP session store. Redis is available as an experimental session-only mode. See [Redis session architecture](docs/scaling/redis-session-architecture.md).

The provisional MVP model bundles all required meetings into one Section, has one Offering per course/term, and uses AND prerequisites. These are synthetic project rules. The default enrollment path implements late section locking as described in [AD-13](docs/architecture-decisions.md#ad-13--prepare-immutable-academic-facts-before-the-hot-section-lock).

## Requirements and verified technology versions

- JDK **21 LTS**; use a supported current patch and set JAVA_HOME. Verification includes Temurin 21.0.13+9.
- Docker with Linux containers and Docker Compose v2; the verification host uses Docker Desktop on macOS arm64. Windows 11 users need a working Docker Desktop/WSL2 Linux-container setup.
- Internet access for the first Gradle/dependency/image download. Gradle is provided by the checked-in wrapper; no global Gradle installation is required.

| Component | Selected version |
| --- | --- |
| Kotlin | 2.4.21 |
| Spring Boot | 4.1.2 |
| Gradle wrapper / Kotlin DSL | 9.7.0 |
| PostgreSQL | 18.6, digest-pinned official Alpine image |
| Flyway core / PostgreSQL module | 12.4.0 |
| HikariCP | 7.0.2 |
| PostgreSQL JDBC | 42.7.14 |
| Spring Security / Spring Session JDBC | 7.1.2 / 4.1.1 |
| JUnit Jupiter / Testcontainers | 5.14.4 / 2.0.5 |

[Verified versions and official sources](docs/technology-versions.md) explain compatibility, BOM alignment, Kotlin/Gradle selection and the JUnit 5 boundary. Tests use standalone JUnit 5/JDBC/Testcontainers and direct SpringApplication startup, without Spring 7's JUnit 6 extension. Wrapper checksum and dependency lockfiles are tracked.

## Local environment configuration

From the repository root, copy `.env.example` to `.env` and replace **both** password placeholders with distinct random local passwords. The `.env` file is ignored by Git; use single-line unquoted URL-safe values so the shell, PowerShell loader and Compose interpret them consistently. No real university credentials belong here.

This task generated an ignored local `.env` with random passwords for verification; do not overwrite an existing environment just to repeat setup. Compose reads `.env` explicitly; Gradle/Java need the variables loaded into their process environment.

| Variable | Purpose / default |
| --- | --- |
| `DB_URL` | Required JDBC URL; example `jdbc:postgresql://127.0.0.1:15432/sdu_registration` |
| `DB_NAME`, `DB_PORT` | Compose database and loopback port; defaults `sdu_registration`, `15432`; keep DB_URL consistent |
| `DB_MIGRATION_USERNAME` | Dedicated local cluster bootstrap/migration login, example `portal_migrator` |
| `DB_MIGRATION_PASSWORD` | Required local migration password; no application default |
| `DB_RUNTIME_PASSWORD` | Required separate `portal_runtime` password; no application default |
| `SPRING_PROFILES_ACTIVE` | `dev` for loopback development; tests explicitly use `test` |
| `SERVER_ADDRESS`, `SERVER_PORT` | Defaults `127.0.0.1`, `8080` |
| `DB_POOL_MAX_SIZE`, `DB_POOL_MIN_IDLE` | Development defaults `10`, `1`; not measured capacity recommendations |
| `DB_CONNECTION_TIMEOUT_MS` | Development bounded pool wait, default `3000`; not an enrollment SLO |

Runtime username is deliberately fixed to the reviewed `portal_runtime` role. Flyway uses separate credentials and owns all academic schema creation. A Flyway afterMigrate callback configures the runtime login/password using bound values and PostgreSQL quoting, keeping secrets out of migration checksums. This is local bootstrap behavior; a shared deployment should run migrations/credential provisioning separately from the runtime and review administrative statement logging.

### macOS / Linux

```sh
# First-time setup only; edit the copy before starting PostgreSQL.
cp .env.example .env

# Load a reviewed local environment, never an untrusted downloaded file.
set -a
. ./.env
set +a

# On macOS, select an installed JDK 21 when needed.
export JAVA_HOME=$(/usr/libexec/java_home -v 21)

docker compose --env-file .env -f infrastructure/compose.yaml up -d --wait postgres
cd backend
./gradlew migrate
./gradlew bootRun
```

On Linux, set JAVA_HOME to the installed JDK 21 path instead of using `/usr/libexec/java_home`. For an existing configured `.env`, omit the copy step. The PostgreSQL volume uses the PostgreSQL 18 image layout at `/var/lib/postgresql`.

### Windows 11 / PowerShell

For **local load testing on another Windows computer**, use the [WSL2 quickstart](docs/windows-local-quickstart.md). It prepares the isolated benchmark stack, generates fresh local credentials and runs only a 10-VU smoke when explicitly requested.

```powershell
# First-time setup only; edit the copy and set JAVA_HOME to JDK 21.
Copy-Item .env.example .env

# Import key/value data without evaluating it as PowerShell code.
Get-Content .env | ForEach-Object {
    if ($_ -match '^\s*([^#=]+)=(.*)$') {
        [Environment]::SetEnvironmentVariable($matches[1].Trim(), $matches[2], 'Process')
    }
}

docker compose --env-file .env -f infrastructure/compose.yaml up -d --wait postgres
Set-Location backend
.\gradlew.bat migrate
.\gradlew.bat bootRun
```

Windows commands are documented for portability but were not executed on the macOS verification host. The future Windows/i5-12400F/32-GB benchmark is a different workload/environment, not established by these integration tests.

## Migrations and startup

`migrate` applies and validates the same versioned resources that Boot uses at startup. Repeating it on an up-to-date database applies zero migrations. Do not edit an applied migration; add a new version. Flyway clean is disabled.

| Migration | Responsibility |
| --- | --- |
| V1 | Dedicated schemas, NOLOGIN owner/support roles and restricted database access |
| V2 | All 22 physical tables, PK/UNIQUE/FK/CHECK constraints |
| V3 | Seven additional required indexes, including both active unique indexes; PK/UNIQUE already supply 43 backing indexes |
| V4 | One-way episode guard, immutable claim/completion guard and deferred terminal idempotency guard |
| V5 | Runtime column privileges, bounded expiry helpers and two redacted observer views |
| V6 | F4-01 correction: reject numeric NaN in five credit/grade fields |
| V7 | F5-01 correction: restricted student-term FOR UPDATE capability without academic UPDATE privilege |
| V8 | Optional ordered RAM/RabbitMQ SQL projector; preserves atomic audit and durable idempotency |

The Docker bootstrap login has local cluster administration rights to create roles; it is not the application's datasource identity. `portal_runtime` has no DDL, audit editing, academic DELETE or unrestricted idempotency DELETE permission. Expired results can be removed only through reviewed helpers. See [Data integrity](docs/data-integrity.md).

After startup, these low-detail endpoints return `{"status":"UP"}` on a healthy instance:

```sh
curl http://127.0.0.1:8080/actuator/health
curl http://127.0.0.1:8080/actuator/health/liveness
curl http://127.0.0.1:8080/actuator/health/readiness
```

Readiness includes database access; liveness does not depend on PostgreSQL. Structured console logs use JSON ECS. A valid UUID `X-Correlation-ID` is preserved, an invalid/missing one is replaced, and safe problem responses contain correlation. Synthetic login/CSRF/logout endpoints are implemented with no default password. Provision local accounts using the [Phase 5 runbook](docs/phase5-verification.md#local-synthetic-accounts-and-manual-api-use).

## Build and tests

With Docker running, from `backend/`:

```sh
./gradlew test
./gradlew bootJar
# Or run the normal verification/packaging lifecycle:
./gradlew build
```

PowerShell equivalents use `.\gradlew.bat`. Tests need no `.env`: they start a separate digest-pinned PostgreSQL container, create isolated databases per primitive scenario or application test class, apply all migrations, use synthetic fixtures, check state/invariants, then drop those test databases. They do not use the Compose data volume, H2, real SDU data or a hosted service. A missing Docker daemon is a test failure, not silently skipped verification.

Tests are serial because account roles belong to the shared test cluster. The suite reads `docs/database.md` as a declared Gradle input and compares actual columns/types/nullability/defaults, PKs, unique keys, composite FKs, CHECK counts and required index definitions. Targeted rejection/rollback/locking tests check the critical behavior. Test-only HTTP probes live outside the production scan root and are excluded from the production jar.

Reports: `backend/build/reports/tests/test/index.html` and `backend/build/test-results/test/`. Build output is ignored. Lockfiles and the wrapper are tracked. Core service and HTTP tests exercise enrollment, withdrawal, rollback, academic decisions, replay and concurrency. Actual OS-process termination/TCP commit-ack loss and 5,000-user performance are not tested. Run the explicit allocation fixture with `./gradlew correctnessStress`; its report is `backend/build/reports/phase5-correctness-stress.txt`.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| Docker socket/daemon unavailable | Start Docker Desktop or the supported Linux daemon and verify `docker version` before Gradle tests/Compose |
| Wrong Java or toolchain selected | Check JAVA_HOME, `./gradlew --version`, `./gradlew javaToolchains` and user `~/.gradle/gradle.properties`. A command override such as `./gradlew "-Dorg.gradle.java.home=$JAVA_HOME" test` selects the intended JDK without changing global files. |
| Missing configuration placeholder | Load `.env` into the Gradle/Java process; Compose loading the file alone does not export variables |
| Port occupied | Change DB_PORT and DB_URL together, or SERVER_PORT; keep development services on loopback |
| Bootstrap password fails after changing `.env` | PostgreSQL image initialization variables apply only to a new volume. Rotate the existing database role deliberately; do not delete the volume as a routine fix. |
| Flyway checksum/validation failure | Restore the applied migration and add a new version; do not use clean or delete history to hide drift |
| Runtime login is disabled | Run `migrate` with the migration and runtime environment values; afterMigrate provisions the reviewed runtime credential |
| Test image compatibility error | Use the checked-in Testcontainers configuration, which explicitly declares the pinned official PostgreSQL digest compatible |
| First dependency/image download fails | Restore network/registry access and rerun. No test is recorded passed until the actual run completes. |

Stop the application with Ctrl+C. From the root, `docker compose --env-file .env -f infrastructure/compose.yaml stop postgres` stops PostgreSQL while preserving its data. Do not use `down -v` for routine shutdown.

## Isolated HTTP benchmark

The benchmark stack uses a separate Compose project, PostgreSQL volume, credentials and loopback ports. It does not use the ordinary `.env` database. Follow [monitoring setup](infrastructure/monitoring/README.md), then [load-testing methodology](docs/load-testing-methodology.md). The methodology includes guarded seed/reset, small reproducible k6 commands, AD-13 comparison, real fault tests, raw artifacts and stepwise commands for another computer (Linux/macOS or WSL2). Prepare fresh sessions; do not copy local credentials/session files to another host.

The local benchmark stack was stopped after measurement. Starting it again uses the retained isolated volume and observations. Do not use `down -v` for routine benchmark reset.

## Documentation and repositories

- [Ordered RabbitMQ-to-PostgreSQL projection](docs/memory-rabbitmq-projector-optimization.md): optional SQL function, preserved atomicity and drain-first recovery.
- [RAM/RabbitMQ 2,500-VU experiment](docs/memory-rabbitmq-load.md): strict HTTP, projection and SQL gates, limits and measured results.
- [RAM decisions and RabbitMQ experimental mode](docs/memory-rabbitmq-experiment.md): opt-in synthetic smoke, recovery semantics, cost limits and cleanup.

- [Phase 6 measured report](docs/phase6-performance-report.md), [methodology](docs/load-testing-methodology.md), [bottleneck analysis](docs/bottleneck-analysis.md), [graphs](docs/phase6-assets/baseline.png)
- [Phase 5 verification and local account runbook](docs/phase5-verification.md)
- [Requirements](docs/requirements.md), [Architecture](docs/architecture.md), [ADRs](docs/architecture-decisions.md)
- [Database dictionary](docs/database.md), [ERD](docs/database-erd.md), [Locking protocol](docs/database-concurrency.md), [Integrity and planned fixtures](docs/data-integrity.md)
- [Enrollment design](docs/enrollment-design.md), [API proposal](docs/api-specification.md), [Security](docs/security.md), [Scaling](docs/scaling-strategy.md), [Performance hypotheses](docs/performance.md)
- [Phase 12 JVM, SQL and resource benchmarks](docs/scaling/phase12-jvm-sql-scaling.md)
- [JDBC contention isolation experiment](docs/scaling/jdbc-contention-fix.md)
- [Verified technology versions](docs/technology-versions.md), [Phase 4 executed verification](docs/phase4-verification.md)

Repositories: [Backend](https://github.com/Ruslando123/SDU_Portal_Backend) owns backend code, infrastructure and shared documentation; [Frontend](https://github.com/Ruslando123/SDU_Portal_Frontend) owns the Next.js application. The backend pins its tested frontend revision as a submodule. See the [repository workflow](docs/repository-layout.md).

## Development roadmap

| Phase | Status |
| --- | --- |
| 1 — Requirements Engineering | Documented; FR/NFR/BR/CT/Q identifiers retained |
| 2 — Detailed System Design | Documented; AD-01 through AD-12 retained |
| 3 — PostgreSQL Database Design | Preliminary review accepted; implementation revealed and corrected F4-01 |
| 4 — Backend Foundation & Database Verification | Implemented and verified; original tests retained |
| 5 — Authentication & Core Enrollment | Implemented and verified; previous cases retained |
| 6 — HTTP baseline, observability, reliability | Small profile measured; isolated failure checks pass; stop for review |
| Later work | Larger benchmarks on another host, further SRE/academic/admin decisions and frontend under separate authorization |

## Performance scope

The Phase 6 report records only executed profiles and their limitations. The 5,000-active-user performance hypothesis remains unverified. See the [original verification](docs/autonomous-verification.md) for the 2,000-attempt HTTP hotspot, the [cloud scaling verification](docs/cloud-scaling-verification.md) for the earlier cloud ladder, and the [Phase 9 architecture comparison](docs/scaling/architecture-comparison.md) for the infrastructure experiments.
