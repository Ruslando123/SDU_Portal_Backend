SET ROLE portal_owner;

CREATE FUNCTION operations.delete_expired_outcome(p_operation_id uuid)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $cleanup$
DECLARE deleted_count integer;
BEGIN
    DELETE FROM operations.idempotency_records
    WHERE operation_id = p_operation_id AND state = 'COMPLETED'
      AND expires_at <= pg_catalog.clock_timestamp();
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count = 1;
END
$cleanup$;

CREATE FUNCTION operations.cleanup_expired_outcomes(p_limit integer)
RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $cleanup$
DECLARE outcome record; deleted_count integer := 0;
BEGIN
    IF p_limit IS NULL OR p_limit < 1 OR p_limit > 1000 THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Cleanup batch size must be between 1 and 1000';
    END IF;
    FOR outcome IN
        SELECT operation_id FROM operations.idempotency_records
        WHERE state = 'COMPLETED' AND expires_at <= pg_catalog.clock_timestamp()
        ORDER BY expires_at, operation_id LIMIT p_limit FOR UPDATE SKIP LOCKED
    LOOP
        IF operations.delete_expired_outcome(outcome.operation_id) THEN
            deleted_count := deleted_count + 1;
        END IF;
    END LOOP;
    RETURN deleted_count;
END
$cleanup$;

REVOKE ALL ON FUNCTION operations.delete_expired_outcome(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION operations.cleanup_expired_outcomes(integer) FROM PUBLIC;

GRANT USAGE ON SCHEMA students, identity, catalog, rules, sections, enrollment, operations, audit
TO portal_runtime, portal_fixture;
GRANT USAGE ON SCHEMA operations TO portal_cleanup;

GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA students, identity, catalog, rules, sections, enrollment, operations, audit
TO portal_runtime;
GRANT UPDATE (display_name) ON students.students TO portal_runtime;
GRANT UPDATE (password_hash, enabled, auth_version) ON identity.test_accounts TO portal_runtime;
GRANT UPDATE (title) ON catalog.courses TO portal_runtime;
GRANT UPDATE (title, credits, publication_status, draft_revision, published_at)
ON catalog.course_offerings TO portal_runtime;
GRANT UPDATE (capacity, enrolled_count, publication_status, published_at, config_version)
ON sections.course_sections TO portal_runtime;
GRANT UPDATE (configuration_state, revision, updated_at) ON rules.term_policy_gates TO portal_runtime;
GRANT UPDATE (is_closed, closed_at) ON rules.registration_windows TO portal_runtime;
GRANT UPDATE (status, withdrawn_at, withdraw_window_id) ON enrollment.enrollments TO portal_runtime;
GRANT UPDATE (state, decided_at, response_status, response_code, response_body, expires_at)
ON operations.idempotency_records TO portal_runtime;
GRANT UPDATE, DELETE ON identity.spring_session, identity.spring_session_attributes TO portal_runtime;

GRANT EXECUTE ON FUNCTION enrollment.guard_episode_update(), operations.guard_outcome_update(),
    operations.require_terminal_outcome() TO portal_runtime;
GRANT EXECUTE ON FUNCTION operations.delete_expired_outcome(uuid) TO portal_runtime, portal_cleanup;
GRANT EXECUTE ON FUNCTION operations.cleanup_expired_outcomes(integer) TO portal_cleanup;

-- Isolated fixture role is not a service login and has no DDL/ownership rights.
GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE
ON ALL TABLES IN SCHEMA students, identity, catalog, rules, sections, enrollment, operations, audit
TO portal_fixture;

-- Observer receives metadata access only; no credentials, bodies or student identifiers.
CREATE VIEW sections.occupancy_summary AS
SELECT section_id, capacity, enrolled_count FROM sections.course_sections;
CREATE VIEW operations.outcome_summary AS
SELECT operation_type, state, count(*) AS outcome_count
FROM operations.idempotency_records GROUP BY operation_type, state;
GRANT USAGE ON SCHEMA sections, operations TO portal_observer;
GRANT SELECT ON sections.occupancy_summary, operations.outcome_summary TO portal_observer;

RESET ROLE;
