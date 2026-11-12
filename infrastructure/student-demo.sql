-- Apply after synthetic-demo.sql in a fresh disposable database only.
\getenv student_password SDU_DEMO_STUDENT_PASSWORD
\getenv admin_password SDU_DEMO_ADMIN_PASSWORD
BEGIN;
INSERT INTO students.students VALUES
('10000000-0000-4000-8000-000000000021','synthetic-peer','Synthetic Demo Peer',clock_timestamp());
INSERT INTO students.eligibility_snapshots VALUES
('10000000-0000-4000-8000-000000000022','10000000-0000-4000-8000-000000000021','synthetic-demo-v1','COMPLETE','ACTIVE',clock_timestamp());
INSERT INTO enrollment.student_terms VALUES
('10000000-0000-4000-8000-000000000021','10000000-0000-4000-8000-000000000002','10000000-0000-4000-8000-000000000003','SYNTHETIC','10000000-0000-4000-8000-000000000022',NULL,clock_timestamp());
INSERT INTO identity.test_accounts VALUES
('10000000-0000-4000-8000-000000000011','10000000-0000-4000-8000-000000000001','synthetic-demo',
 '{bcrypt}' || crypt(:'student_password',gen_salt('bf',10)),'STUDENT',true,1,clock_timestamp()),
('10000000-0000-4000-8000-000000000012',NULL,'synthetic-admin',
 '{bcrypt}' || crypt(:'admin_password',gen_salt('bf',10)),'ADMIN',true,1,clock_timestamp()),
('10000000-0000-4000-8000-000000000023','10000000-0000-4000-8000-000000000021','synthetic-peer',
 '{bcrypt}' || crypt(:'student_password',gen_salt('bf',10)),'STUDENT',true,1,clock_timestamp());
UPDATE catalog.courses SET code='CSC-210', title='Data Structures' WHERE course_id='10000000-0000-4000-8000-000000000006';
UPDATE catalog.course_offerings SET title='Data Structures' WHERE offering_id='10000000-0000-4000-8000-000000000007';
UPDATE sections.course_sections SET section_code='A01' WHERE section_id='10000000-0000-4000-8000-000000000008';
CREATE TEMP TABLE demo_offerings(id uuid, offering uuid, section uuid, code text, title text, credits numeric, capacity integer) ON COMMIT DROP;
INSERT INTO demo_offerings VALUES
('11000000-0000-4000-8000-000000000001','12000000-0000-4000-8000-000000000001','13000000-0000-4000-8000-000000000001','CSC-240','Database Systems',4,24),
('11000000-0000-4000-8000-000000000002','12000000-0000-4000-8000-000000000002','13000000-0000-4000-8000-000000000002','MAT-205','Discrete Mathematics',3,20),
('11000000-0000-4000-8000-000000000003','12000000-0000-4000-8000-000000000003','13000000-0000-4000-8000-000000000003','CSC-310','Software Architecture',5,18),
('11000000-0000-4000-8000-000000000005','12000000-0000-4000-8000-000000000005','13000000-0000-4000-8000-000000000005','QA-101','Single-seat Workshop',1,1);
INSERT INTO catalog.courses SELECT id,code,title,clock_timestamp() FROM demo_offerings;
INSERT INTO catalog.course_offerings
SELECT offering,'10000000-0000-4000-8000-000000000002',id,title,credits,'PUBLISHED',1,clock_timestamp() FROM demo_offerings;
INSERT INTO rules.offering_policy_eligibility
SELECT offering,'10000000-0000-4000-8000-000000000003','10000000-0000-4000-8000-000000000002' FROM demo_offerings;
INSERT INTO sections.course_sections(section_id,offering_id,term_id,section_code,capacity,publication_status,published_at,config_version)
SELECT section,offering,'10000000-0000-4000-8000-000000000002','A01',capacity,'PUBLISHED',clock_timestamp(),1 FROM demo_offerings;
INSERT INTO sections.course_sections(section_id,offering_id,term_id,section_code,capacity,publication_status,published_at,config_version)
VALUES ('13000000-0000-4000-8000-000000000004','10000000-0000-4000-8000-000000000007','10000000-0000-4000-8000-000000000002','B01',18,'PUBLISHED',clock_timestamp(),1);
INSERT INTO sections.instructors VALUES
('14000000-0000-4000-8000-000000000001','Demo Instructor A'),
('14000000-0000-4000-8000-000000000002','Demo Instructor B'),
('14000000-0000-4000-8000-000000000003','Demo Instructor C');
INSERT INTO sections.section_instructors VALUES
('10000000-0000-4000-8000-000000000008','14000000-0000-4000-8000-000000000001'),
('13000000-0000-4000-8000-000000000004','14000000-0000-4000-8000-000000000002'),
('13000000-0000-4000-8000-000000000001','14000000-0000-4000-8000-000000000002'),
('13000000-0000-4000-8000-000000000002','14000000-0000-4000-8000-000000000003'),
('13000000-0000-4000-8000-000000000003','14000000-0000-4000-8000-000000000001'),
('13000000-0000-4000-8000-000000000005','14000000-0000-4000-8000-000000000001');
INSERT INTO sections.section_meetings
SELECT gen_random_uuid(), section_id, start_at, start_at + interval '90 minutes', 'LECTURE', true, 'Demo Campus'
FROM (
 SELECT section_id, date_trunc('day', clock_timestamp(), 'Asia/Almaty') + interval '2 days 9 hours' + row_number() OVER (ORDER BY section_id) * interval '2 hours' AS start_at
 FROM sections.course_sections WHERE section_id::text LIKE '13000000-%'
) demo_meetings;
COMMIT;
