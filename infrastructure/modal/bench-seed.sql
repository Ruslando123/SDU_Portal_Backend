-- Fixed Cloud 8C fixture after synthetic-demo.sql. The extra five students warm the login profile.
\getenv student_password SDU_STUDENT_PASSWORD
\getenv bench_students SDU_BENCH_STUDENTS
\getenv section_capacity SDU_BENCH_SECTION_CAPACITY
BEGIN;
INSERT INTO students.students (student_id, student_number, display_name, created_at)
SELECT ('80000000-0000-4000-8000-' || lpad(i::text,12,'0'))::uuid,
       'cloud-bench-' || lpad(i::text,greatest(3,length(i::text)),'0'), 'Synthetic Cloud Student', clock_timestamp()
FROM generate_series(1, :bench_students) AS i;

WITH verifier AS MATERIALIZED (
    SELECT '{bcrypt}' || crypt(:'student_password',gen_salt('bf',10)) AS hash
)
INSERT INTO identity.test_accounts
    (principal_id, student_id, login_name, password_hash, role, enabled, auth_version, created_at)
SELECT ('81000000-0000-4000-8000-' || lpad(i::text,12,'0'))::uuid, s.student_id,
       s.student_number, verifier.hash, 'STUDENT', true, 1, clock_timestamp()
FROM generate_series(1, :bench_students) AS i
JOIN students.students s ON s.student_number='cloud-bench-' || lpad(i::text,greatest(3,length(i::text)),'0')
CROSS JOIN verifier;

INSERT INTO students.eligibility_snapshots
SELECT ('82000000-0000-4000-8000-' || lpad(i::text,12,'0'))::uuid, s.student_id,
       'cloud-bench-v1','COMPLETE','ACTIVE',clock_timestamp()
FROM generate_series(1, :bench_students) AS i
JOIN students.students s ON s.student_number='cloud-bench-' || lpad(i::text,greatest(3,length(i::text)),'0');

INSERT INTO enrollment.student_terms
SELECT e.student_id,'10000000-0000-4000-8000-000000000002','10000000-0000-4000-8000-000000000003',
       'SYNTHETIC', e.snapshot_id, NULL, clock_timestamp()
FROM students.eligibility_snapshots e WHERE e.source_version='cloud-bench-v1';

INSERT INTO sections.course_sections
(section_id,offering_id,term_id,section_code,capacity,publication_status,published_at,config_version)
SELECT ('90000000-0000-4000-8000-' || lpad(i::text,12,'0'))::uuid,
       '10000000-0000-4000-8000-000000000007','10000000-0000-4000-8000-000000000002',
       'BENCH' || lpad(i::text,2,'0'), :section_capacity, 'PUBLISHED', clock_timestamp(), 1
FROM generate_series(1,20) AS i;

INSERT INTO sections.section_meetings
SELECT ('91000000-0000-4000-8000-' || lpad(i::text,12,'0'))::uuid,
       ('90000000-0000-4000-8000-' || lpad(i::text,12,'0'))::uuid,
       clock_timestamp() + interval '3 days', clock_timestamp() + interval '3 days 1 hour',
       'LECTURE', true, 'Synthetic Room'
FROM generate_series(1,20) AS i;
COMMIT;
ANALYZE;
