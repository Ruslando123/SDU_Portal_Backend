# Verified technology versions

Checked against official documentation and resolved artifacts on November 6, 2026. These are compatible stable selections, not a claim that every library is the newest independently released artifact. Spring Boot's BOM supplies aligned runtime versions; the supported Kotlin/Gradle intersection and the explicit JUnit 5 requirement determine the overrides.

| Component | Selected version | Source / reason |
| --- | --- | --- |
| Java | Java 21 LTS, Temurin 21.0.13+9 recommended verification runtime; bytecode/toolchain target 21 | [Temurin release](https://github.com/adoptium/temurin21-binaries/releases/tag/jdk-21.0.13%2B9); supported by the selected Spring Boot. The Phase 4 37-test build, Phase 5 suite/allocation and packaged smoke use 21.0.13+9; installed 21.0.12.1+1 also passed the initial suite. |
| Kotlin JVM / Spring plugin / stdlib / reflect | 2.4.21 | [Kotlin Gradle compatibility](https://kotlinlang.org/docs/gradle-configure-project.html); runtime Kotlin BOM aligned with the compiler |
| Spring Boot | 4.1.2 | [System requirements](https://docs.spring.io/spring-boot/4.1/system-requirements.html); stable line, Java 21 and Gradle 9 supported |
| Gradle Kotlin DSL / wrapper | 9.7.0 | [Release](https://docs.gradle.org/9.7.0/release-notes.html); within Kotlin 2.4.21's documented range. Wrapper distribution SHA-256 is pinned. |
| PostgreSQL server | 18.6 | [Release notes](https://www.postgresql.org/docs/release/18.6/); same digest-pinned official Alpine image in Compose and Testcontainers |
| Flyway core / PostgreSQL module | 12.4.0 | Spring Boot 4.1.2 BOM selection, verified against PostgreSQL 18.6; [PostgreSQL module](https://documentation.red-gate.com/fd/postgresql-database-277579325.html) |
| HikariCP | 7.0.2 | Boot BOM, resolved and used by the backend |
| PostgreSQL JDBC | 42.7.14 | Boot BOM, used by application and database verification |
| Spring Security | 7.1.2 | Boot BOM |
| Spring Session JDBC | 4.1.1 | Boot BOM; actual save/find/rotation/delete tested against the approved two tables |
| JUnit Jupiter / Platform | 5.14.4 / 1.14.4 | [JUnit 5 release notes](https://docs.junit.org/5.14.4/release-notes.html); explicit user requirement |
| Testcontainers PostgreSQL | 2.0.5 | Boot BOM-aligned explicit version; [PostgreSQL module](https://java.testcontainers.org/modules/databases/postgres/) |
| Docker / Compose on verification host | Engine 29.1.2, Desktop 4.54.0, Compose 2.40.3 | Observed local tools; not required exact client versions for other developers |

Runtime BOM details: [Spring Boot managed coordinates](https://docs.spring.io/spring-boot/4.1/appendix/dependency-versions/coordinates.html). Concrete resolution is committed in `backend/gradle.lockfile` and the buildscript lockfile. Update dependencies intentionally and regenerate locks; ordinary runs do not use `--write-locks`.

## JUnit 5 compatibility boundary

Boot 4's default test stack recommends JUnit 6. This project deliberately does not import `spring-boot-starter-test`, Spring's JUnit extension or `@SpringBootTest`. JUnit 5 runs standalone JDBC/Flyway/Testcontainers integration tests; HTTP tests start/close SpringApplication directly and use Java HttpClient. An enforced test-only JUnit BOM selects 5.14.4. Production Spring dependencies stay on Boot's BOM. The actual suite validates this combination; it is not a general claim that Spring 7's JUnit 6 extension supports a downgraded engine.

## Reproducibility and environment

The official PostgreSQL image is pinned to manifest digest `sha256:123b17998ccaf47e53b502e8c6a1e79afd8f579c6f3eec83c563da026f957395`. Testcontainers explicitly declares that digest-pinned image compatible with its PostgreSQL module. The wrapper checksum was compared to the downloaded official Gradle archive.

Verification is on macOS arm64 with Docker Linux containers. Windows 11/WSL2 commands are documented, but were not executed on this host. Inspect `./gradlew --version` and `./gradlew javaToolchains`: a user-level `org.gradle.java.home` can override JAVA_HOME. Use a per-command `-Dorg.gradle.java.home=...` override when selecting a particular installed JDK; do not commit machine-specific paths.

Phase 5 retains the same locked dependency versions. Authentication uses Spring Security 7.1.2 session fixation/CSRF integration and Spring Session JDBC 4.1.1; no new external service or runtime dependency was added.
