SET ROLE portal_owner;

CREATE FUNCTION enrollment.guard_episode_update() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $guard$
BEGIN
    IF ROW(NEW.enrollment_id, NEW.student_id, NEW.term_id, NEW.policy_id,
           NEW.offering_id, NEW.section_id, NEW.enroll_window_id, NEW.enrolled_at)
       IS DISTINCT FROM
       ROW(OLD.enrollment_id, OLD.student_id, OLD.term_id, OLD.policy_id,
           OLD.offering_id, OLD.section_id, OLD.enroll_window_id, OLD.enrolled_at) THEN
        RAISE EXCEPTION USING ERRCODE = '23514',
            CONSTRAINT = 'guard_enrollment_episode', MESSAGE = 'Enrollment episode identity is immutable';
    END IF;
    IF OLD.status <> 'ACTIVE' OR NEW.status <> 'WITHDRAWN' THEN
        RAISE EXCEPTION USING ERRCODE = '23514',
            CONSTRAINT = 'guard_enrollment_episode', MESSAGE = 'Only ACTIVE to WITHDRAWN is permitted';
    END IF;
    RETURN NEW;
END
$guard$;

CREATE TRIGGER guard_enrollment_episode
BEFORE UPDATE ON enrollment.enrollments
FOR EACH ROW EXECUTE FUNCTION enrollment.guard_episode_update();

CREATE FUNCTION operations.guard_outcome_update() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $guard$
BEGIN
    IF ROW(NEW.operation_id, NEW.principal_id, NEW.operation_type,
           NEW.operation_version, NEW.idempotency_key, NEW.request_fingerprint,
           NEW.target_type, NEW.target_id, NEW.target_term_id, NEW.created_at)
       IS DISTINCT FROM
       ROW(OLD.operation_id, OLD.principal_id, OLD.operation_type,
           OLD.operation_version, OLD.idempotency_key, OLD.request_fingerprint,
           OLD.target_type, OLD.target_id, OLD.target_term_id, OLD.created_at) THEN
        RAISE EXCEPTION USING ERRCODE = '23514',
            CONSTRAINT = 'guard_idempotency_transition', MESSAGE = 'Idempotency claim identity is immutable';
    END IF;
    IF OLD.state <> 'CLAIMED' OR NEW.state <> 'COMPLETED' THEN
        RAISE EXCEPTION USING ERRCODE = '23514',
            CONSTRAINT = 'guard_idempotency_transition', MESSAGE = 'Only claim completion is permitted';
    END IF;
    RETURN NEW;
END
$guard$;

CREATE TRIGGER guard_idempotency_transition
BEFORE UPDATE ON operations.idempotency_records
FOR EACH ROW EXECUTE FUNCTION operations.guard_outcome_update();

CREATE FUNCTION operations.require_terminal_outcome() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $guard$
BEGIN
    -- Both insertion and completion events inspect the final row, never stale NEW.state.
    IF NOT EXISTS (
        SELECT 1 FROM operations.idempotency_records
        WHERE operation_id = NEW.operation_id AND state = 'COMPLETED'
    ) THEN
        RAISE EXCEPTION USING ERRCODE = '23514',
            CONSTRAINT = 'require_terminal_idempotency', MESSAGE = 'An idempotency claim must complete before commit';
    END IF;
    RETURN NULL;
END
$guard$;

CREATE CONSTRAINT TRIGGER require_terminal_idempotency
AFTER INSERT OR UPDATE ON operations.idempotency_records
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION operations.require_terminal_outcome();

REVOKE ALL ON FUNCTION enrollment.guard_episode_update() FROM PUBLIC;
REVOKE ALL ON FUNCTION operations.guard_outcome_update() FROM PUBLIC;
REVOKE ALL ON FUNCTION operations.require_terminal_outcome() FROM PUBLIC;

RESET ROLE;
