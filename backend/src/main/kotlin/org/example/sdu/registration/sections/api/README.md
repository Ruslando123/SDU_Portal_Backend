# sections public boundary

Sections, meetings, instructors and bounded occupancy.

Phase 5 exposes SectionFacts for immutable read snapshots and SectionAllocation for ordered lock/allocate/release. Only Enrollment may call the allocation port. Other modules must not import internal implementations. Ownership/dependencies are defined in [Architecture](../../../../../../../../../../docs/architecture.md#modules-and-ownership).
