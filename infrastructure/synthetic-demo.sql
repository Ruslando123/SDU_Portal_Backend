-- Optional isolated local fixture. Apply once with the migrator, after Flyway.
-- No accounts/passwords are included. Provision STUDENT/ADMIN accounts with syntheticAccount.
-- The static IDs describe synthetic fixtures only, not credentials or real SDU data.
BEGIN;
INSERT INTO students.students VALUES ('10000000-0000-4000-8000-000000000001','synthetic-demo','Synthetic Demo Student',clock_timestamp());
INSERT INTO catalog.academic_terms VALUES ('10000000-0000-4000-8000-000000000002','SYNTHETIC-DEMO',current_date,current_date+180,'Asia/Almaty');
INSERT INTO rules.term_policy_gates VALUES ('10000000-0000-4000-8000-000000000002','PREPARING',1,clock_timestamp());
INSERT INTO rules.registration_policies VALUES ('10000000-0000-4000-8000-000000000003','10000000-0000-4000-8000-000000000002','SYNTHETIC',1,18.00,clock_timestamp());
INSERT INTO rules.registration_windows VALUES
('10000000-0000-4000-8000-000000000004','10000000-0000-4000-8000-000000000003','ENROLL',clock_timestamp()-interval '1 hour',clock_timestamp()+interval '7 days',false,NULL),
('10000000-0000-4000-8000-000000000005','10000000-0000-4000-8000-000000000003','WITHDRAW',clock_timestamp()-interval '1 hour',clock_timestamp()+interval '7 days',false,NULL);
INSERT INTO catalog.courses VALUES ('10000000-0000-4000-8000-000000000006','SYNTHETIC101','Synthetic Course',clock_timestamp());
INSERT INTO catalog.course_offerings VALUES ('10000000-0000-4000-8000-000000000007','10000000-0000-4000-8000-000000000002','10000000-0000-4000-8000-000000000006','Synthetic Offering',3.00,'PUBLISHED',1,clock_timestamp());
INSERT INTO sections.course_sections (section_id,offering_id,term_id,section_code,capacity,publication_status,published_at,config_version) VALUES
('10000000-0000-4000-8000-000000000008','10000000-0000-4000-8000-000000000007','10000000-0000-4000-8000-000000000002','DEMO',30,'PUBLISHED',clock_timestamp(),1);
INSERT INTO sections.section_meetings VALUES ('10000000-0000-4000-8000-000000000009','10000000-0000-4000-8000-000000000008',clock_timestamp()+interval '2 days',clock_timestamp()+interval '2 days 1 hour','LECTURE',true,'Synthetic Room');
INSERT INTO students.eligibility_snapshots VALUES ('10000000-0000-4000-8000-000000000010','10000000-0000-4000-8000-000000000001','synthetic-demo-v1','COMPLETE','ACTIVE',clock_timestamp());
INSERT INTO rules.offering_policy_eligibility VALUES ('10000000-0000-4000-8000-000000000007','10000000-0000-4000-8000-000000000003','10000000-0000-4000-8000-000000000002');
INSERT INTO enrollment.student_terms VALUES ('10000000-0000-4000-8000-000000000001','10000000-0000-4000-8000-000000000002','10000000-0000-4000-8000-000000000003','SYNTHETIC','10000000-0000-4000-8000-000000000010',NULL,clock_timestamp());
UPDATE rules.term_policy_gates SET configuration_state='PUBLISHED',revision=2,updated_at=clock_timestamp() WHERE term_id='10000000-0000-4000-8000-000000000002';
COMMIT;
