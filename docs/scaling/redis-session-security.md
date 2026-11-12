# Redis session security and cutover

Redis stores only Spring Session data. Cookie flags are Secure, HttpOnly and SameSite=Lax. Login rotates the session ID, CSRF is required for state changes, and logout deletes the session. The session idle timeout is 30 minutes and the authenticated lifetime is eight hours. The per-request PostgreSQL account and authorization-version check provides revocation across instances independently of session storage.

Redis and JDBC are mutually exclusive at application startup. Invalid mode values fail startup. Redis uses a bounded command timeout and rejects queued commands when disconnected. An unavailable or unreadable session fails closed. A storage failure after a business commit can leave an uncertain HTTP response. The client must preserve the original Idempotency-Key and recover through PostgreSQL's durable outcome once it has a valid session. App and infrastructure logs must omit credentials, cookies, raw session attributes and CSRF values.

The Redis application ACL permits only Spring Session hash, TTL, rotation and deletion commands under the session namespace. The observer can read INFO. The default user is disabled, and both users have independent credentials. The benchmark Redis listener is restricted to loopback. A shared Redis endpoint across hosts requires TLS with the CA and host verified, authenticated ACL users and network isolation.

## Serialization and rolling upgrades

The experimental store uses Spring Session's Java-serialized session values with a restrictive input filter, explicit application and framework class allowlist, and input size and graph limits. The namespace suffix identifies the format generation. Both backend instances must use a compatible build and generation. Verify serialization in both directions before a rolling deployment. Incompatible versions require a fresh namespace and a new login.

## Switching and reverting

1. Drain all backend instances before switching. Run a single store mode across the fleet.
2. For JDBC to Redis, deploy with a fresh Redis namespace and scoped credentials. Existing users must sign in again.
3. Before rollback to JDBC, invalidate the old JDBC sessions while traffic is drained by removing the existing rows from `identity.spring_session` through an authorized database operator. The foreign key cascades to session attributes. This step prevents a still-valid pre-cutover cookie from becoming valid again.
4. Switch every instance to JDBC, verify a pre-cutover cookie returns 401, then verify login, CSRF, logout and same-key recovery. Retire the old Redis namespace and rotate its credentials when the rollout decision is final.

A transient Redis interruption can preserve sessions if the process and in-memory state survive. A process restart loses the ephemeral session data and requires login. The cloud benchmark uses one backend. Cross-instance sharing and outage behavior are covered by the real Redis/PostgreSQL JVM test.

[Spring Session Redis configuration](https://docs.spring.io/spring-session/reference/4.2/configuration/redis.html) · [Redis ACL reference](https://redis.io/docs/latest/management/security/acl/)
