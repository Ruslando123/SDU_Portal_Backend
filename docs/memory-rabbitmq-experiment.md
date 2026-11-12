# RAM decisions and RabbitMQ journal experiment

This opt-in implementation preserves the synchronous PostgreSQL mode as the default. Historical migrations V1–V7 and benchmark reports are unchanged. It is an experiment with synthetic data, one decision owner, and a frozen academic configuration. It is not a production deployment or evidence of 2,500-user capacity.

Measured smoke outcome and preserved failures: [verification report](memory-rabbitmq-verification.md).

## Decision and persistence boundaries

1. Spring Security validates the live account, session, role and CSRF token as before. Redis stores HTTP sessions. Account revocation is not cached in RAM.
2. One bounded admission gate and one fair in-process decision lock protect student/term state, memberships, credits, timetable conflicts, section occupancy and idempotency together.
3. The existing academic rule evaluator runs on the loaded snapshot. The PostgreSQL clock is read over a reserved owner connection; no new JDBC checkout is needed for that decision. This is not a zero-database HTTP request: authentication and static catalog reads still access PostgreSQL.
4. A persistent, mandatory-routed, versioned final decision is published to a durable RabbitMQ quorum queue. The Java client waits for publisher confirmation and checks for an unroutable return.
5. Only after confirmation are all affected RAM structures changed and HTTP success returned. The response identifies `decisionAuthority: RABBITMQ_CONFIRMED`. The student is accepted; this is not a pending application awaiting academic review.
6. A single ordered projector commits the section counter, enrollment history, audit and durable idempotency outcome in one PostgreSQL transaction. It ACKs the message only after `TransactionTemplate` has returned from COMMIT. Projection does not re-evaluate a registration window at a later time.

The final PostgreSQL transaction remains atomic. HTTP success now means durable acceptance by the broker, not completion of that PostgreSQL transaction. This change in the source of truth is fundamental and must be understood by every consumer of the data.

## Recovery and safety

- A failed or unknown publisher confirm closes the engine to further decisions. There is no blind compensation of a seat. The original key can be retried after recovery.
- Before loading RAM, the new owner projects every outstanding message to PostgreSQL. Recovery runs on `ApplicationReadyEvent` before readiness accepts traffic. Modal migrations and fixture seeding finish before the JVM starts. Lazy initialization is permitted only in integration tests; loading the snapshot inside a concurrent transactional read can exhaust the JDBC pool.
- A PostgreSQL transaction-scoped advisory lock and an exclusive RabbitMQ owner queue reject a second experimental owner. Automatic client recovery and automatic leader failover are disabled. A lost connection requires recovery, not continued use of stale RAM.
- Immutable audit IDs are permanent inbox identities. An event payload hash is compared on redelivery. This handles COMMIT followed by lost ACK without depending on expiring idempotency rows or applying the seat change twice. No new schema migration is needed.
- Normal retries use the original principal, operation type, target and Idempotency-Key. A changed target with the same key is rejected. Recovered responses retain their original serialized body.
- Enrollment history, schedule membership and section occupancy read from RAM immediately. Available-course filtering uses the RAM occupancy overlay before SQL pagination. PostgreSQL observers may see projection lag.
- Administrative API access is disabled in this mode. The owner holds shared term gates for the bounded experiment, freezing changes through the existing administrative lock protocol. Direct SQL writes, synchronous enrollment writers, and a second application must never share this experimental database.
- Backlog, runtime, admission and in-memory outcome count are bounded. Saturation returns an explicit failure, never a false success or silent message drop. A projection error leaves the message unacknowledged and closes decisions.
- Idempotency entries loaded at startup remain available throughout the bounded run, including entries that would otherwise expire during that run. This can extend replay availability; it does not shorten the normal retention guarantee.

## Limits of this first implementation

A single serial decision owner and synchronous confirms deliberately make ordering explicit. They may become a throughput bottleneck. No high-load improvement is claimed before measurement. Partitioning owners safely by student and section would require a separate correctness design.

The Modal broker has one quorum member on temporary Sidecar storage. It survives an application/worker restart while its storage exists. It does **not** tolerate loss of the Sandbox host, deletion of the Sandbox, or loss of the broker disk. PostgreSQL and RabbitMQ Sidecars share the main Sandbox's host and aggregate CPU/RAM budget. They are container isolation, not independent failure domains. Modal Volumes are not treated as a substitute for a supported durable broker deployment.

Production acceptance requires durable RabbitMQ storage and a quorum distributed across failure domains, recovery and fencing design, operational backups, monitoring and a failure campaign. None of these are implied by a successful temporary cloud smoke.

## Local verification

Use JDK 21 and Docker:

```sh
cd backend
./gradlew test bootJar
```

`MemoryEnrollmentIntegrationTest` uses real PostgreSQL and RabbitMQ 4.4.0. It tests last-seat concurrency, duplicate keys, ownership, credit limits, schedule conflicts, closed windows, live account revocation, CSRF, immediate RAM reads, loss of publisher confirmation, restart with backlog, COMMIT before lost ACK, rejection of a second owner, and concurrent readers after eager recovery with a two-connection pool. Local broker traffic is test-only plaintext on Testcontainers ports; the Modal path uses verified TLS.

The `LostConfirmConfiguration` belongs to test sources only. It publishes to the real broker before injecting a confirmation timeout; the subsequent recovery verifies the durable result. These are application failure tests, not a claim that a single broker survives host failure.

## Modal smoke

Prepare the existing verified JRE/k6/gateway assets and build the new JAR. The official Modal SDK is pinned to 1.6.3. The documented Sidecar API supported by this SDK is used; no public PostgreSQL or RabbitMQ listener is created.

```sh
python infrastructure/modal/memory_benchmark.py --available-credit-usd 10
python infrastructure/modal/memory_benchmark.py --available-credit-usd 10 --execute
```

The first command is read-only and prints current official rates and a cost estimate. The second requires an idle Modal environment and obtains the existing global runner lock. It starts exactly one Sandbox and one single-use k6 Function, both in `us-east`. The main Sandbox contains HAProxy, the JVM and Redis; PostgreSQL and RabbitMQ are private Sidecars. The only externally routed port is the authenticated benchmark gateway on 8080 behind a Modal encrypted tunnel. HAProxy verifies the backend TLS certificate; JDBC verifies the PostgreSQL certificate; the AMQP Java client verifies the RabbitMQ certificate and hostname. Management routes are denied by the gateway.

Limits: 4 CPU / 8 GiB total Sandbox allocation, 600 seconds hard Sandbox timeout; 1 CPU / 1 GiB generator, 75 seconds function timeout, 45 seconds startup timeout, one container, no retries or autoscaling. k6 runs 10 VUs with one complete journey each, maximum 30 seconds. It retains CSRF and keys and verifies replay after withdrawal. The prepared dataset is the existing synthetic cloud smoke fixture, not the high-load dataset.

The conservative allocation estimate includes the 1.75 regional multiplier, image-build reserve and 1 GiB egress allowance, and must stay below $0.50 before credits. User-reported available credits are recorded separately from costs; applied credits in a monthly invoice are not misrepresented as remaining credits. A smoke is not run if the configured credit budget or current rates fail preflight.

After HTTP completion the runner waits for projection; it never deletes active enrollments to make assertions pass. All 30 mutation/replay HTTP responses are compared with the 20 unique durable PostgreSQL outcomes, including principal, operation, target, status and full response. All eight existing SQL invariants must pass, with zero final active enrollments. `finally` terminates the Sandbox and its Sidecars, stops the App and verifies no active containers or Sandboxes remain. All evidence is saved under `infrastructure/.local/modal/runs/<run-id>/`.

Stop a run with `modal app stop <app-id>` and verify with `modal app list --json`, `modal container list --json`, and the supported SDK inventory:

```sh
python -c "import modal; print([(s.object_id, s.poll()) for s in modal.Sandbox.list(app_id='APP_ID')])"
```

An active Sandbox has `poll() is None`. Do not discard a broker containing unprojected accepted operations in a persistent deployment; drain and reconcile first. This runner always disposes synthetic test data after retaining evidence.

Attach attributable cost when the billing report has settled:

```sh
python infrastructure/modal/billing.py --date YYYY-MM-DD --run-id <run-id>
```

Performance Lab labels these runs as a memory experiment, shows confirmed/projected counts and reconciliation, and excludes them from synchronous-throughput comparisons. Missing measurements and actual billing remain No Data until collected.

## References

- [RabbitMQ publisher confirms and consumer acknowledgements](https://www.rabbitmq.com/docs/confirms)
- [RabbitMQ quorum queue durability and replication](https://www.rabbitmq.com/docs/quorum-queues)
- [RabbitMQ Java client, TLS and recovery](https://www.rabbitmq.com/client-libraries/java-api-guide)
- [Modal Sandbox Sidecars and shared host resources](https://modal.com/docs/guide/sandbox-sidecars)
- [Modal pricing](https://modal.com/pricing)
