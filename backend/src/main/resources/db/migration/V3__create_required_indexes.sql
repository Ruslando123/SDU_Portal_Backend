SET ROLE portal_owner;

CREATE UNIQUE INDEX ux_enrollment_active_section_student ON enrollment.enrollments (section_id, student_id) WHERE status = 'ACTIVE';
CREATE UNIQUE INDEX ux_enrollment_active_student_offering ON enrollment.enrollments (student_id, offering_id) WHERE status = 'ACTIVE';
CREATE INDEX ix_enrollment_student_term_history ON enrollment.enrollments (student_id, term_id, enrolled_at DESC, enrollment_id);
CREATE INDEX ix_idempotency_expiry ON operations.idempotency_records (expires_at, operation_id) WHERE state = 'COMPLETED';
CREATE INDEX ix_audit_enrollment_time ON audit.audit_events (enrollment_id, decided_at, audit_id) WHERE enrollment_id IS NOT NULL;
CREATE INDEX spring_session_ix2 ON identity.spring_session (expiry_time);
CREATE INDEX spring_session_ix3 ON identity.spring_session (principal_name);

RESET ROLE;
