-- Run inside the new Sandbox after the existing synthetic demo fixture.
\getenv student_password SDU_STUDENT_PASSWORD
BEGIN;
INSERT INTO students.students (student_id, student_number, display_name, created_at)
SELECT ('80000000-0000-4000-8000-' || lpad(i::text,12,'0'))::uuid,
       'cloud-smoke-' || lpad(i::text,2,'0'), 'Synthetic Cloud Student', clock_timestamp()
FROM generate_series(1,10) AS i;

WITH verifier AS MATERIALIZED (
    SELECT '{bcrypt}' || crypt(:'student_password',gen_salt('bf',10)) AS hash
)
INSERT INTO identity.test_accounts
    (principal_id, student_id, login_name, password_hash, role, enabled, auth_version, created_at)
SELECT ('81000000-0000-4000-8000-' || lpad(i::text,12,'0'))::uuid, s.student_id,
       s.student_number, verifier.hash, 'STUDENT', true, 1, clock_timestamp()
FROM generate_series(1,10) AS i
JOIN students.students s ON s.student_number='cloud-smoke-' || lpad(i::text,2,'0')
CROSS JOIN verifier;

INSERT INTO students.eligibility_snapshots
SELECT ('82000000-0000-4000-8000-' || lpad(i::text,12,'0'))::uuid, s.student_id,
       'cloud-smoke-v1','COMPLETE','ACTIVE',clock_timestamp()
FROM generate_series(1,10) AS i
JOIN students.students s ON s.student_number='cloud-smoke-' || lpad(i::text,2,'0');

INSERT INTO enrollment.student_terms
SELECT e.student_id,'10000000-0000-4000-8000-000000000002','10000000-0000-4000-8000-000000000003',
       'SYNTHETIC', e.snapshot_id, NULL, clock_timestamp()
FROM students.eligibility_snapshots e WHERE e.source_version='cloud-smoke-v1';
COMMIT;
ANALYZE;
