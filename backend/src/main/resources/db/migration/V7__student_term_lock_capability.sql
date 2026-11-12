SET ROLE portal_owner;

-- F5-01: SELECT FOR UPDATE requires UPDATE privilege. Do not grant academic mutation
-- merely to coordinate a student: expose only the ordered lock/read capability.
CREATE FUNCTION enrollment.lock_student_term(p_student_id uuid, p_term_id uuid)
RETURNS TABLE (policy_id uuid, eligibility_snapshot_id uuid, credit_limit_override numeric)
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $lock$
    SELECT st.policy_id, st.eligibility_snapshot_id, st.credit_limit_override
    FROM enrollment.student_terms AS st
    WHERE st.student_id = p_student_id AND st.term_id = p_term_id
    FOR UPDATE OF st
$lock$;

REVOKE ALL ON FUNCTION enrollment.lock_student_term(uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION enrollment.lock_student_term(uuid, uuid) TO portal_runtime;

RESET ROLE;
