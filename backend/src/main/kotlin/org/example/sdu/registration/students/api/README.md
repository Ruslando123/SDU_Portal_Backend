# students public boundary

Synthetic student profiles and immutable eligibility evidence.

Phase 5 exposes StudentEvidence and immutable pinned AcademicEvidence/Completion snapshots. Its JDBC adapter joins the caller transaction. Other modules must not import internal implementations. Ownership/dependencies are defined in [Architecture](../../../../../../../../../../docs/architecture.md#modules-and-ownership).
