-- Read-only independent checks. Run against the fixture database after the workload commits.
-- Each violations result must be zero. These queries are not a snapshot of a running workload.
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
SELECT count(*) AS capacity_or_ledger_violations FROM (
    SELECT s.section_id FROM sections.course_sections s
    LEFT JOIN enrollment.enrollments e ON e.section_id=s.section_id AND e.status='ACTIVE'
    GROUP BY s.section_id,s.capacity,s.enrolled_count
    HAVING s.enrolled_count<0 OR s.enrolled_count>s.capacity OR s.enrolled_count<>count(e.enrollment_id)
) v;
SELECT count(*) AS duplicate_section_violations FROM (
    SELECT student_id,section_id FROM enrollment.enrollments WHERE status='ACTIVE'
    GROUP BY student_id,section_id HAVING count(*)>1
) v;
SELECT count(*) AS duplicate_offering_violations FROM (
    SELECT student_id,offering_id FROM enrollment.enrollments WHERE status='ACTIVE'
    GROUP BY student_id,offering_id HAVING count(*)>1
) v;
SELECT count(*) AS credit_violations FROM (
    SELECT st.student_id,st.term_id FROM enrollment.student_terms st
    JOIN rules.registration_policies p ON p.policy_id=st.policy_id
    LEFT JOIN enrollment.enrollments e ON e.student_id=st.student_id AND e.term_id=st.term_id AND e.status='ACTIVE'
    LEFT JOIN catalog.course_offerings o ON o.offering_id=e.offering_id
    GROUP BY st.student_id,st.term_id,st.credit_limit_override,p.credit_limit
    HAVING coalesce(sum(o.credits),0)>coalesce(st.credit_limit_override,p.credit_limit)
) v;
SELECT count(*) AS required_meeting_violations FROM enrollment.enrollments a
JOIN enrollment.enrollments b ON a.student_id=b.student_id AND a.term_id=b.term_id AND a.enrollment_id<b.enrollment_id
JOIN sections.section_meetings x ON x.section_id=a.section_id AND x.is_required
JOIN sections.section_meetings y ON y.section_id=b.section_id AND y.is_required
WHERE a.status='ACTIVE' AND b.status='ACTIVE' AND x.starts_at<y.ends_at AND y.starts_at<x.ends_at;
SELECT count(*) AS nonterminal_outcome_violations FROM operations.idempotency_records
WHERE state<>'COMPLETED' OR response_body->>'code'<>response_code;
SELECT count(*) AS missing_enrollment_audit_violations FROM enrollment.enrollments e
WHERE NOT EXISTS (SELECT 1 FROM audit.audit_events a WHERE a.enrollment_id=e.enrollment_id AND a.event_type='ENROLLED');
SELECT count(*) AS missing_withdrawal_audit_violations FROM enrollment.enrollments e WHERE e.status='WITHDRAWN'
AND NOT EXISTS (SELECT 1 FROM audit.audit_events a WHERE a.enrollment_id=e.enrollment_id AND a.event_type='WITHDRAWN');
SELECT response_code,count(*) AS outcomes FROM operations.idempotency_records GROUP BY response_code ORDER BY response_code;
COMMIT;
