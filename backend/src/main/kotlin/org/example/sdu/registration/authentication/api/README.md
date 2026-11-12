# authentication public boundary

Authentication and synthetic account/session boundary.

Phase 5 exposes CurrentActor and the immutable Actor DTO. Password verification, session revocation and the offline synthetic-account CLI remain internal. Other modules must not import internal implementations. Ownership/dependencies are defined in [Architecture](../../../../../../../../../../docs/architecture.md#modules-and-ownership).
