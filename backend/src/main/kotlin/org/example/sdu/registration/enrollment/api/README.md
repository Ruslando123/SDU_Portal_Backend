# enrollment public boundary

Future root enrollment transactions and retained membership episodes.

Phase 5 exposes EnrollmentCommands for idempotent enroll/withdraw and OwnEnrollments for identity-scoped read views. Schedule uses read ports only; commands derive identity from Authentication. Other modules must not import internal implementations. Ownership/dependencies are defined in [Architecture](../../../../../../../../../../docs/architecture.md#modules-and-ownership).
