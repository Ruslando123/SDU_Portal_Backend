# Security architecture

Status: Phase 5 synthetic Authentication, ownership, CSRF and JDBC session lifecycle implemented and tested, November 6, 2026; no security certification. See [Phase 5 verification](phase5-verification.md). Only synthetic accounts/data are supported; real SDU accounts and SSO are outside scope.

## Authentication boundary and test account handling

Spring Security is the planned backend HTTP boundary. All protected requests resolve an opaque session to a server-owned internal principal and fixed student/admin roles. Next.js renders the result; neither JavaScript nor arbitrary request headers/body can assert a trusted student ID or role. Ownership checks are repeated in the application use case, including replay and withdrawal.

Select predefined synthetic test accounts with a local credential verifier rather than a free-form impersonation endpoint. Future fixtures contain synthetic student/admin mappings and a test-only password-verifier representation; use a supported adaptive password hash when implemented, with local test secrets supplied outside Git. There is no real credential collection, university scraping, production SSO login or reuse of university passwords. Provisioned roles are controlled by fixture/admin setup, never a self-registration request field.

A clearly named test-auth profile is enabled explicitly only in the isolated MVP/test environment and must refuse use as a real-account production authentication mode. The backend refuses startup without an explicit dev or test profile. The supported provider authenticates only preprovisioned identity.test_accounts using adaptive hashes; there is no public signup or real-account flow. Q-01's MVP identity choice is resolved; production identity lifecycle/provider integration remains a separate future decision.

## Session and token lifecycle

Choose an opaque server-side session cookie and **PostgreSQL-backed shared session storage** for the design. This avoids making Redis mandatory and supports another backend instance resolving the same session without sticky routing. Spring Session supports relational HttpSession storage; see [Spring Session HttpSession integration](https://docs.spring.io/spring-session/reference/http-session.html). Phase 3 selects the standard `identity.spring_session` and `identity.spring_session_attributes` layout in [Database](database.md#session-schema-compatibility). Nullable principal-name metadata is not a domain FK or authorization grant; validate the current account and auth_version separately. Phase 4 pins Spring Session JDBC 4.1.1, disables automatic session DDL, and verifies save/find/rotation/delete against Flyway-owned tables.

- Rotate the session identifier after login/privilege transition; expire/revoke it on logout or account disablement according to the selected policy. Resolve current account/role revocation state on protected requests and replay.
- Use Secure, HttpOnly, host-scoped cookies with explicit SameSite and path settings for the intended deployment. Login/logout and unsafe cookie-authenticated operations require CSRF protection, including replay requests. Validate origin/redirect handling where applicable.
- Do not place session identifiers, long-lived bearer credentials or university passwords in localStorage, URLs, logs, metrics or idempotency fingerprints. A CSRF token is distinct from the session secret and may be obtained through a reviewed browser API.
- The isolated MVP uses a 30-minute idle timeout and an 8-hour absolute authenticated lifetime. Each protected API request checks enabled/auth_version/role/student mapping. Login is limited locally to 8 simultaneous verifications and 60 attempts per minute; these conservative per-instance controls are not a shared-campus abuse strategy. An expired session yields authentication failure; after login, an unresolved intention retains its original idempotency key within retention.
- Session resolution/persistence uses short authentication transactions, outside domain locks. Any session-store read/write overhead and cleanup is included in connection/load budgeting. If session persistence fails after an enrollment commits, do not compensate the enrollment: recover the session and replay the same key.
- Revocation prevents future authentication decisions. An already admitted transaction may finish; logout is not a promise to cancel database work already in progress. Stronger instantaneous cancellation would need an explicit additional protocol.

These are project choices informed by [OWASP Session Management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html), not a claim of completed verification. The dev profile explicitly disables Secure for local loopback HTTP; defaults remain Secure/HttpOnly/SameSite=Lax, and tests verify both configurations; a shared deployment uses TLS and protected cookies.

## Student and administrator authorization

| Actor | Allowed scope | Required denial |
| --- | --- | --- |
| Anonymous | Test login and explicitly public/static content only | No own-schedule, mutation, audit or admin access |
| Student | Published catalog/windows and own enrollments/schedule/withdrawal | No other student's data, role changes, catalog writes or administrative overrides |
| Administrator | Approved Catalog/Sections/Academic Rules/Student Management commands and pre-opening Enrollment provisioning through Administration APIs | No invariant bypass, arbitrary repository/SQL access, or automatic access to unrelated student academic details |
| Operational observer | Restricted metrics and approved diagnostic views | No enrollment write privilege or exposed session/credential data |

Use server-side role checks at endpoints and use-case boundaries, plus object ownership for every own-resource query/mutation/replay. For a withdrawal, discover target routing without locking only after authenticated ownership filtering, then recheck protected ownership/state in the root transaction. Unknown/not-owned resources return a non-enumerating not-found/forbidden policy consistently. A valid resource ID or idempotency key alone grants no access.

Admin commands use explicit version preconditions and deduplication rules, then the same domain invariants and term/section ordering as student commands. Administration orchestrates owning APIs; it cannot edit Enrollment tables directly. Real-account onboarding and live academic overrides are outside the MVP.

## API abuse controls and data protection

Bound login attempts, mutation submissions, request/body/query/page sizes and backend work. Use principal-aware limits after authentication and coarse edge limits before it; IP limits account for shared campus/NAT clients. [Scaling strategy](scaling-strategy.md#rate-limiting-and-a-possible-virtual-waiting-room) defines local versus cluster limits and `429`/`503` behavior. Rate limiting supplements, rather than replaces, uniqueness and idempotency. An unavailable shared limiter cannot silently open unlimited work.

Use parameterized SQL/persistence APIs, safe error mapping and safe rendering of catalog content. Apply narrowly scoped CORS only if a cross-origin need is introduced; same-origin is the baseline. Restrict forwarding-header trust to the proxy. Data services, management endpoints, Prometheus and Grafana are not public student interfaces.

Runtime DB access is least-privileged, migration privileges are separate, and no correction credential is a normal registration path. Secret material is injected outside Git and excluded from responses/logs. Synthetic records are labeled and generated for tests; minimize identifying fixture fields and never copy production data into benchmarks. Future real-data encryption, backup access/retention and legal obligations need university data-owner review; no jurisdiction/compliance claim is made here.

## Audit versus diagnostic logs

Commit audit for a successful academic/admin mutation in the same PostgreSQL transaction as its effects. Record internal actor identity, operation, safe target/change metadata, result, protected decision time and correlation identifier; these sensitive audit records have restricted access. Stable business denials can commit audit with their idempotency outcome and no academic mutation. A replay may produce a diagnostic replay event, but must not append a second academic state-change event.

Audit persistence failure aborts the domain write. External log shipping or a Prometheus scrape failure does not become a remote transaction participant. Failed authentication/authorization and transient failed transactions have redacted security/diagnostic evidence rather than a success audit. Never log passwords, cookie/session secrets, full idempotency keys, raw transcript/grades or entire replay bodies. Metric labels contain bounded route/outcome names, not actor/student IDs or raw resource URLs.

## Required controls and later acceptance cases

SEC identifiers are preserved from Phase 1; [Phase 4 verification](phase4-verification.md) covers a foundation subset, not the complete pilot acceptance criteria.

| ID | Requirement | Acceptance criterion before pilot |
| --- | --- | --- |
| SEC-01 | Verify synthetic authentication and session lifecycle server side. | Missing/invalid/expired/revoked sessions fail; valid predefined test students/admins work; login rotates IDs, logout revokes later access, and re-authenticated replay retains the principal-scoped original key. No real-account integration exists. |
| SEC-02 | Enforce role and ownership on endpoints, use cases and replay. | Student A cannot read/withdraw/replay Student B's records or call admin commands by changing IDs, body fields or headers; admin domain writes cannot bypass invariants. Revocation follows the specified admitted-request policy. |
| SEC-03 | Protect transport, cookies, CSRF and rendering. | Shared deployment uses TLS and reviewed cookie attributes; unsafe requests without valid CSRF proof fail; CORS/proxy trust is bounded; untrusted catalog text is rendered safely. |
| SEC-04 | Bound input/work and use safe persistence. | Oversized/malformed requests and malicious query values fail safely; login/mutation limits, queue bounds, body/page limits and deadlines are observable. Replica rollout verifies aggregate admission rather than multiplying local quotas. |
| SEC-05 | Isolate secrets, runtime privileges and operational endpoints. | No secrets in Git/logs/client storage; runtime and migration privileges differ; data/management services are restricted; test-auth cannot masquerade as production SSO. |
| SEC-06 | Preserve durable audit and redact diagnostics. | Successful mutations have one state-change audit coupled to domain commit; audit/result failure rolls everything back; replay adds no duplicate academic event; logs/metrics omit credentials, raw keys and academic records. Audit retention/access is explicitly decided. |

The Phase 3 [role/guard/deletion design](data-integrity.md#roles-privileges-and-direct-mutation-protection) specifies database privileges and their trusted-runtime boundary. Runtime module privacy is enforced by owning APIs/static checks, not assumed from sharing schema namespaces.

## Outstanding security decisions

Synthetic credentials are provisioned offline with syntheticAccount, using an operator-supplied unique secret outside Git and the delegating encoder default BCrypt verifier. The local runbook is in [Phase 5 verification](phase5-verification.md). Shared credential distribution, cluster abuse limits, audit retention/access and backup recovery still require pilot decisions. Real university identity integration and personal-data obligations require a separate approved scope. Phase 4 installs dependencies and verifies JDBC session storage with small synthetic fixtures. No real university credentials/data, paid service or OpenAI API is used.

## Implemented browser contract

GET /api/v1/auth/csrf returns the framework's masked token, headerName and parameterName and may create a pre-auth session. POST /api/v1/auth/login accepts URL-encoded username/password plus that CSRF header. Form authentication rotates the session ID, erases credentials and saves only the serializable principal (principal/student/role/auth_version), authorities and authenticatedAt. Fetch a new CSRF token after login/logout because Spring rotates it. POST /api/v1/auth/logout requires CSRF and invalidates the session. GET /api/v1/me and /api/v1/auth/me return the authenticated identity. The HTTP suite demonstrates a second backend reading the same JDBC session and replaying the same committed operation.

The default cookie is host scoped, Secure, HttpOnly, SameSite=Lax; only cookie session tracking is enabled. No Basic/Bearer authentication, CORS wildcard, credentials in URLs or client-selected role/student proof is accepted. Password verification is limited to 72 UTF-8 bytes for BCrypt. Session attributes use Spring Session's JDK serialization in the trusted database; untrusted database writers/production identity and serializer hardening require a separate security review.

Implementation follows [Spring Security session management](https://docs.spring.io/spring-security/reference/servlet/authentication/session-management.html) and [CSRF endpoint guidance](https://docs.spring.io/spring-security/reference/servlet/exploits/csrf.html). Health/liveness are anonymous and skip account database checks, so liveness does not acquire a database dependency. Metrics/management endpoints remain closed except low-detail health.
