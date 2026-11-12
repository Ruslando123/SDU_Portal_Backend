# Final verification

| Status | Scope |
| --- | --- |
| Implemented | Student Portal, reproducible disposable demo, private live Performance Lab, cloud evidence including scoped correlation and time series |
| Independently Tested | Browser sign-in and sign-out, catalog search, instructor and section selection, enrollment, withdrawal, timetable and credits, loading and retry, CSRF, actual database session expiry, same-key replay after a lost committed response and two students competing for one seat |
| Independently Tested | PostgreSQL consistency and concurrent allocation, archived and live Performance Lab, Modal administrator session and CSRF enforcement, local startup and cleanup |
| Independently Tested | Separate read and mixed 200-VU diagnostics over the authenticated encrypted tunnel. Each passed warm-up, measurement, SQL and cleanup |
| Failed | Stable qualification on standard Sandbox Connect. The 502 reproduced before authenticated gateway ingress and the exact provider hop remains unresolved |
| Not Tested | Mixed 200 VUs on Sandbox Connect, cloud 500–5,000 VUs, maximum stable open-model throughput and production deployment |

The browser suite covers mobile and desktop workflows and uses the actual Spring Boot, PostgreSQL and private Prometheus stack. The single simulated browser failure exercises error rendering and retry. The lost-response test forwards the request to Spring, lets PostgreSQL commit the enrollment, suppresses the first response and verifies the exact replay on a new page load. The final-seat test uses two independently authenticated browser sessions and verifies real HTTP 201 and 409 outcomes before releasing the seat. The session-expiry test updates a real disposable Spring Session row and verifies that the next protected read returns the browser to sign-in.

Verification reran 93 JVM tests and the separate PostgreSQL concurrency test. Eleven Playwright tests, seven frontend unit tests, nine Python runner tests, the Go gateway tests and the Next.js production build passed. The demo teardown check verified that both listeners, the disposable PostgreSQL container and its generated credentials file were removed. Flyway V1–V7 and all 523 retained Phase 6 artifacts match the original SHA-256 manifests.

The release gate is split between local functional demonstration and cloud performance qualification. The former is ready. The reproduced pre-ingress 502 blocks standard-route qualification. One independent pass per profile verifies the alternate path for the documented synthetic workload. [Incident analysis](bottleneck-analysis-final.md) · [Capacity evidence](performance-capacity-report.md) · [Demo](final-demo-guide.md) · [Known limitations](known-limitations.md)

## Modal billing and cleanup

Modal attributed `$0.19487875` before credits to 6 of 6 release diagnostic attempts. The retained project-wide billing receipts total `$0.57146671` before credits. All 6 diagnostic runs verified exact App cleanup. The final workspace check found zero active Apps and containers. [Modal pricing](https://modal.com/pricing).
