# Memory / RabbitMQ verification — 2026-11-11

The experimental mode is implemented and the corrected Modal smoke passed. Keep it **opt-in for synthetic experiments**; retain synchronous PostgreSQL as the default. This is a correctness and integration result. Sustained throughput, 2,000/2,500 VUs and host-loss recovery with this architecture are **Not Tested**.

Implementation and commands: [memory-rabbitmq-experiment.md](memory-rabbitmq-experiment.md).

## Successful cloud result

Run: `modal-20261111T113054Z-8ae450`. Region: `us-east`. The matched artifact SHA-256 is `9b217ab48d8042fbbc8a8b9b9733626e31dba4563e78b0e9eb9d556000ea70aa`.

| Measurement | Observed |
| --- | ---: |
| Actual participating VUs | 10 |
| Authenticated synthetic accounts | 10 |
| Measured HTTP requests | 50 |
| Completed journeys | 10 |
| Measured duration | 0.671293569 s |
| HTTP RPS in this short smoke | 74.4830612253 |
| HTTP p95 | 273.1858965 ms |
| HTTP p99 | 283.1248533 ms |
| Unexpected HTTP errors | 0 |
| Broker-confirmed unique decisions | 20 |
| Projected durable operations | 20 |
| Reconciled mutation/replay HTTP responses | 30 |
| Final active enrollments / occupied seats | 0 / 0 |
| Withdrawn historical enrollment episodes | 10 |
| Immutable audit events | 20 |
| SQL invariant violations | 0 across all eight checks |
| Queue ready / unacknowledged deliveries after drain | 0 / 0 |
| k6 CPU time / peak RSS | 0.08 CPU-s / 131.734 MiB |

The duration is intentionally short: one journey per VU. RPS here is not sustainable throughput or a capacity estimate. There was no 120-second benchmark, no high-load qualification and no direct comparison to the previous 2,000-VU synchronous baseline.

The main Sandbox, PostgreSQL Sidecar and RabbitMQ Sidecar share a 4-CPU / 8-GiB configured budget with a 600-second hard timeout. The JVM uses a 512-MiB heap; the JDBC pool is 10, including one reserved owner connection. Redis stores HTTP sessions. HAProxy verifies local backend TLS; JDBC and the AMQP client verify their private peer certificates. The generator has 1 CPU / 1 GiB, one container and no retries. These resources differ from the historic 16-CPU high-load baseline.

The runtime export captured Hikari active=1 and pending=0 after drain; these are a post-test snapshot, not peak measurements. Sandbox CPU usage and RAM peak were not available through the collected cgroup paths and remain No Data. The archived gateway export did not include the new portal-memory meter family; confirmed/projected counts were independently established from HTTP records, the broker queue and PostgreSQL. No queue-latency or capacity numbers are inferred.

## Correctness evidence

The worker returned final enrollment outcomes after persistent RabbitMQ publisher confirmation. PostgreSQL projection occurred in a separate worker. Each projection atomically changed the seat counter, enrollment history, audit and idempotency outcome; manual ACK followed COMMIT.

The runner compared all 30 POST/DELETE/replay responses against the 20 unique outcomes, including original key, principal, operation type, target, HTTP status and complete response body. It verified all SQL invariants, the empty durable quorum queue and zero active final enrollments. No post-test deletion or compensating enrollment cleanup was used.

Security probes verified rejection of an absent gateway key, an absent student session, exposed health/metrics routes, plaintext PostgreSQL, an incorrect PostgreSQL password and a wrong RabbitMQ certificate hostname. All real test traffic used synthetic accounts and retained CSRF/idempotency behavior.

## Startup defect found and fixed

In the first actual HTTP attempt, ten simultaneous section reads entered PostgreSQL transactions before the lazy RAM initializer ran. Initialization needed an owner connection and another transaction for the snapshot. Readers occupied the available JDBC slots while waiting for the initializer's lock. The backend reported `SQLTransientConnectionException` after approximately the configured 3-second checkout timeout; the engine then failed closed. There were 10 failed reads and no registration decisions.

Recovery now runs during `ApplicationReadyEvent`, before readiness accepts traffic. Modal migrates and seeds the isolated database before starting the JVM. Lazy initialization is test-only. A regression verifies ten concurrent section readers after eager recovery with a two-connection pool. No pool, CPU or timeout increase was used to fix this startup dependency.

A separate Sidecar packaging failure was corrected by baking startup files with `copy=True` before `.build(app)`. Local TLS preflight also caught restrictive file ownership and a root diagnostics/cookie race; configuration ownership and diagnostics execution as the broker user were corrected before the repeat.

## Preserved attempts and cost

| Run | Status | HTTP requests | Unexpected errors | Modal-reported cost before credits | Result |
| --- | --- | ---: | ---: | ---: | --- |
| `modal-20261111T111416Z-79f239` | FAILED | No Data | No Data | $0.00169800 | Sidecar image required copy=True; no load started. |
| `modal-20261111T112146Z-286840` | FAILED | 10 | 10 | $0.02595946 | Lazy RAM recovery competed with transactional readers for JDBC; 10 read failures, no mutations. |
| `modal-20261111T113054Z-8ae450` | PASSED | 50 | 0 | $0.01704269 | Eager recovery before readiness; full HTTP/journal/SQL smoke gate passed. |

Reported total for these three Apps: **$0.04470015 before credits**. Billing attribution uses exact App ID and `sdu_run` tag; observed amounts may settle further. The corrected run's conservative allocation plan was $0.34491024 including image-build reserve and egress allowance, below the $0.50 smoke cap. The user reported $10 available credit. Applied billing credits are not used as proof of a wallet balance.

Two additional local/preflight exits created no App: an unsupported image-builder keyword and an idle guard that incorrectly counted stopped App metadata. These are retained in the local execution logs. They are not passed benchmarks.

Cleanup for every cloud attempt was verified: Apps stopped, zero active Sandboxes and zero active containers. No Modal Volume or externally paid broker/database was created. Historical benchmark directories and Flyway V1–V7 hashes remain unchanged.

## Local and UI validation

- 117 backend tests passed (107 existing tests plus 10 memory/journal integration tests).
- 36 Modal runner unit tests passed, including cost limits and complete HTTP/SQL reconciliation requirements.
- The exact RabbitMQ TLS startup script passed local Docker boot, trusted-host verification and wrong-host rejection.
- 8 frontend data-projection tests and TypeScript checking passed.
- A Chrome/Playwright test verified the Performance Lab memory report and its explicit durability limitations, including the successful cloud report.

The integration tests cover concurrent last-seat allocation, credit/schedule/window checks, ownership, live account revocation, CSRF, same-key retries, immediate RAM reads while projection is paused, lost publisher confirm, backlog recovery, COMMIT before lost ACK, and second-owner rejection. These test process/reply recovery while broker storage survives; they do not simulate loss of a whole host.

## Artifacts

Each run directory under `infrastructure/.local/modal/runs/` retains `report.json`, sanitized logs and available diagnostics. The successful run additionally retains `http-operations.json`, `invariants.json`, `k6-summary.json`, `raw.json.gz`, `k6-console.log` and `metrics.prom`. The server-side Performance Lab loader reads the reports, exposes safe fields and labels the new acceptance semantics separately from synchronous results.

The pre-change source snapshot and migration checksums are under `infrastructure/.local/releases/before-memory-rabbit-20261111/`.

## Remaining production boundary

One RAM owner provides ordering; one disposable broker provides neither replicated host-loss durability nor automatic failover. A crash of the Java application can be recovered from RabbitMQ and PostgreSQL; deletion or loss of the Sandbox host can lose both temporary stores. A durable multi-host quorum deployment and its recovery/fencing design must be verified before real registration data is accepted. Large-scale throughput remains to be measured separately.
