-- F4-01: NUMERIC NaN sorts above finite values and passes a >= 0 check.
SET ROLE portal_owner;
ALTER TABLE rules.registration_policies
    ADD CONSTRAINT ck_policy_credit_limit_finite CHECK (credit_limit <> 'NaN'::numeric);
ALTER TABLE catalog.course_offerings
    ADD CONSTRAINT ck_offering_credits_finite CHECK (credits <> 'NaN'::numeric);
ALTER TABLE catalog.offering_prerequisites
    ADD CONSTRAINT ck_prerequisite_grade_finite CHECK (minimum_grade IS NULL OR minimum_grade <> 'NaN'::numeric);
ALTER TABLE students.completed_courses
    ADD CONSTRAINT ck_completed_grade_finite CHECK (grade_value IS NULL OR grade_value <> 'NaN'::numeric);
ALTER TABLE enrollment.student_terms
    ADD CONSTRAINT ck_student_credit_override_finite CHECK (credit_limit_override IS NULL OR credit_limit_override <> 'NaN'::numeric);
RESET ROLE;
