# Redis HTTP sessions

The application selects its Spring Session repository at startup with `PORTAL_SESSION_STORE=jdbc|redis`. JDBC is the default and uses the existing transaction mode. Redis uses Spring Session 4.1.1's basic `RedisSessionRepository` and Lettuce 7.5.2, pinned by Spring Boot 4.1.2. The basic repository supports direct session lookup and deletion without an indexed principal store or keyspace notification subscriptions. PostgreSQL continues to validate the synthetic account and its authorization version for each authenticated request.

| Data | Authority |
| --- | --- |
| Session ID, last access, CSRF token and Spring Security context | Selected session repository |
| Accounts, eligibility, catalog, seats, enrollments, audit and idempotency outcomes | PostgreSQL |

Redis mode requires an authenticated client. For non-loopback hosts, set `PORTAL_SESSION_REDIS_TLS=true` and install the service CA in the JVM trust store. Lettuce uses full certificate and hostname verification. The configurable endpoint defaults to `127.0.0.1:6379`, and `PORTAL_SESSION_REDIS_USERNAME` defaults to `sdu-session`. Supply `PORTAL_SESSION_REDIS_PASSWORD` through the deployment secret mechanism.

## Modal topology

Redis is an ephemeral process beside Spring Boot in the main Sandbox. It binds to IPv4 loopback. PostgreSQL remains a separate Modal Sidecar on the private bridge using TLS verify-full, SCRAM, fsync, synchronous commit and full-page writes. The benchmark gate, verified direct HTTP TLS, JAR, seed, application pool, generator and PostgreSQL settings are shared by A and B. Both arms have the Redis package installed and only B starts Redis.

The combined Sandbox allocation is 9 Modal CPU cores and 17 GiB, shared by Spring Boot, PostgreSQL and the local gateway. Redis consumes a share of the same allocation in B. Its data memory limit is 256 MiB with `noeviction`, 64 maximum clients and one Redis I/O thread. RSS and CPU are sampled independently and enforced through the shared cgroup. The generator has 4 Modal CPU cores and 4 GiB. The only Modal tunnel is the authenticated benchmark HTTP gateway. Redis credentials and its observer credential are distinct per-run Modal Secrets.

Startup requires Redis PING, ACL and policy checks before the Java process starts. The same generator runs a synthetic session smoke before preparing the measured actors. Local INFO and Prometheus sampling run during load. A process trap terminates the Redis process, and the runner verifies that the App, Sandbox and Sidecar are stopped after each run. The session store uses no persistence or volumes. PostgreSQL durability remains enabled.

[Spring Session Redis reference](https://docs.spring.io/spring-session/reference/guides/boot-redis.html) · [Modal Sidecars](https://modal.com/docs/guide/sandbox-sidecars) · [Redis eviction policy](https://redis.io/docs/latest/develop/reference/eviction/)
