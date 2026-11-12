SET ROLE portal_owner;

-- DBT-01: Student Management.
CREATE TABLE students.students (
    student_id uuid NOT NULL,
    student_number text NOT NULL,
    display_name text NOT NULL,
    created_at timestamptz NOT NULL,
    CONSTRAINT pk_students_students PRIMARY KEY (student_id),
    CONSTRAINT uq_students_number UNIQUE (student_number),
    CONSTRAINT ck_students_01 CHECK (char_length(student_number) BETWEEN 1 AND 64),
    CONSTRAINT ck_students_02 CHECK (char_length(display_name) BETWEEN 1 AND 200)
);

-- DBT-02: Authentication.
CREATE TABLE identity.test_accounts (
    principal_id uuid NOT NULL,
    student_id uuid,
    login_name text NOT NULL,
    password_hash text NOT NULL,
    role text NOT NULL,
    enabled boolean NOT NULL,
    auth_version bigint NOT NULL,
    created_at timestamptz NOT NULL,
    CONSTRAINT pk_identity_test_accounts PRIMARY KEY (principal_id),
    CONSTRAINT uq_test_accounts_student UNIQUE (student_id),
    CONSTRAINT uq_test_accounts_login UNIQUE (login_name),
    CONSTRAINT fk_account_student FOREIGN KEY (student_id) REFERENCES students.students (student_id) ON UPDATE NO ACTION ON DELETE NO ACTION,
    CONSTRAINT ck_test_accounts_01 CHECK (role IN ('STUDENT','ADMIN')),
    CONSTRAINT ck_test_accounts_02 CHECK ((role = 'STUDENT' AND student_id IS NOT NULL) OR (role = 'ADMIN' AND student_id IS NULL)),
    CONSTRAINT ck_test_accounts_03 CHECK (auth_version > 0),
    CONSTRAINT ck_test_accounts_04 CHECK (char_length(login_name) BETWEEN 1 AND 100 AND login_name = lower(login_name)),
    CONSTRAINT ck_test_accounts_05 CHECK (char_length(password_hash) BETWEEN 1 AND 1024)
);

-- DBT-03: Course Catalog.
CREATE TABLE catalog.academic_terms (
    term_id uuid NOT NULL,
    code text NOT NULL,
    starts_on date NOT NULL,
    ends_on date NOT NULL,
    timezone_name text NOT NULL,
    CONSTRAINT pk_catalog_academic_terms PRIMARY KEY (term_id),
    CONSTRAINT uq_terms_code UNIQUE (code),
    CONSTRAINT ck_academic_terms_01 CHECK (ends_on >= starts_on),
    CONSTRAINT ck_academic_terms_02 CHECK (char_length(code) BETWEEN 1 AND 32),
    CONSTRAINT ck_academic_terms_03 CHECK (char_length(timezone_name) BETWEEN 1 AND 100)
);

-- DBT-04: Academic Rules.
CREATE TABLE rules.term_policy_gates (
    term_id uuid NOT NULL,
    configuration_state text NOT NULL,
    revision bigint NOT NULL,
    updated_at timestamptz NOT NULL,
    CONSTRAINT pk_rules_term_policy_gates PRIMARY KEY (term_id),
    CONSTRAINT fk_gate_term FOREIGN KEY (term_id) REFERENCES catalog.academic_terms (term_id) ON UPDATE NO ACTION ON DELETE NO ACTION,
    CONSTRAINT ck_term_policy_gates_01 CHECK (configuration_state IN ('PREPARING','PUBLISHED')),
    CONSTRAINT ck_term_policy_gates_02 CHECK (revision > 0)
);

-- DBT-05: Academic Rules.
CREATE TABLE rules.registration_policies (
    policy_id uuid NOT NULL,
    term_id uuid NOT NULL,
    cohort_code text NOT NULL,
    policy_version integer NOT NULL,
    credit_limit numeric(6,2) NOT NULL,
    created_at timestamptz NOT NULL,
    CONSTRAINT pk_rules_registration_policies PRIMARY KEY (policy_id),
    CONSTRAINT uq_policy_term_cohort_version UNIQUE (term_id, cohort_code, policy_version),
    CONSTRAINT uq_policy_identity_term_cohort UNIQUE (policy_id, term_id, cohort_code),
    CONSTRAINT uq_policy_identity_term UNIQUE (policy_id, term_id),
    CONSTRAINT fk_policy_term FOREIGN KEY (term_id) REFERENCES catalog.academic_terms (term_id) ON UPDATE NO ACTION ON DELETE NO ACTION,
    CONSTRAINT ck_registration_policies_01 CHECK (policy_version > 0),
    CONSTRAINT ck_registration_policies_02 CHECK (credit_limit >= 0),
    CONSTRAINT ck_registration_policies_03 CHECK (char_length(cohort_code) BETWEEN 1 AND 64)
);

-- DBT-06: Academic Rules.
CREATE TABLE rules.registration_windows (
    window_id uuid NOT NULL,
    policy_id uuid NOT NULL,
    operation_type text NOT NULL,
    opens_at timestamptz NOT NULL,
    closes_at timestamptz NOT NULL,
    is_closed boolean NOT NULL,
    closed_at timestamptz,
    CONSTRAINT pk_rules_registration_windows PRIMARY KEY (window_id),
    CONSTRAINT uq_window_policy_operation UNIQUE (policy_id, operation_type),
    CONSTRAINT uq_window_identity_policy UNIQUE (window_id, policy_id),
    CONSTRAINT fk_window_policy FOREIGN KEY (policy_id) REFERENCES rules.registration_policies (policy_id) ON UPDATE NO ACTION ON DELETE NO ACTION,
    CONSTRAINT ck_registration_windows_01 CHECK (operation_type IN ('ENROLL','WITHDRAW')),
    CONSTRAINT ck_registration_windows_02 CHECK (closes_at > opens_at),
    CONSTRAINT ck_registration_windows_03 CHECK ((is_closed AND closed_at IS NOT NULL) OR (NOT is_closed AND closed_at IS NULL))
);

-- DBT-07: Course Catalog.
CREATE TABLE catalog.courses (
    course_id uuid NOT NULL,
    code text COLLATE "C" NOT NULL,
    title text NOT NULL,
    created_at timestamptz NOT NULL,
    CONSTRAINT pk_catalog_courses PRIMARY KEY (course_id),
    CONSTRAINT uq_courses_code UNIQUE (code),
    CONSTRAINT ck_courses_01 CHECK (char_length(code) BETWEEN 1 AND 32 AND code = upper(code)),
    CONSTRAINT ck_courses_02 CHECK (char_length(title) BETWEEN 1 AND 240)
);

-- DBT-08: Course Catalog.
CREATE TABLE catalog.course_offerings (
    offering_id uuid NOT NULL,
    term_id uuid NOT NULL,
    course_id uuid NOT NULL,
    title text NOT NULL,
    credits numeric(6,2) NOT NULL,
    publication_status text NOT NULL,
    draft_revision bigint NOT NULL,
    published_at timestamptz,
    CONSTRAINT pk_catalog_course_offerings PRIMARY KEY (offering_id),
    CONSTRAINT uq_offering_term_course UNIQUE (term_id, course_id),
    CONSTRAINT uq_offering_identity_term UNIQUE (offering_id, term_id),
    CONSTRAINT fk_offering_term FOREIGN KEY (term_id) REFERENCES catalog.academic_terms (term_id) ON UPDATE NO ACTION ON DELETE NO ACTION,
    CONSTRAINT fk_offering_course FOREIGN KEY (course_id) REFERENCES catalog.courses (course_id) ON UPDATE NO ACTION ON DELETE NO ACTION,
    CONSTRAINT ck_course_offerings_01 CHECK (credits >= 0),
    CONSTRAINT ck_course_offerings_02 CHECK (draft_revision > 0),
    CONSTRAINT ck_course_offerings_03 CHECK (char_length(title) BETWEEN 1 AND 240),
    CONSTRAINT ck_course_offerings_04 CHECK ((publication_status = 'DRAFT' AND published_at IS NULL) OR (publication_status = 'PUBLISHED' AND published_at IS NOT NULL))
);

-- DBT-09: Course Sections.
CREATE TABLE sections.course_sections (
    section_id uuid NOT NULL,
    offering_id uuid NOT NULL,
    term_id uuid NOT NULL,
    section_code text NOT NULL,
    capacity integer NOT NULL,
    enrolled_count integer NOT NULL DEFAULT 0,
    publication_status text NOT NULL,
    published_at timestamptz,
    config_version bigint NOT NULL,
    CONSTRAINT pk_sections_course_sections PRIMARY KEY (section_id),
    CONSTRAINT uq_section_term_offering_code UNIQUE (term_id, offering_id, section_code),
    CONSTRAINT uq_section_identity_offering_term UNIQUE (section_id, offering_id, term_id),
    CONSTRAINT fk_section_offering_term FOREIGN KEY (offering_id, term_id) REFERENCES catalog.course_offerings (offering_id, term_id) ON UPDATE NO ACTION ON DELETE NO ACTION,
    CONSTRAINT ck_course_sections_01 CHECK (capacity >= 0),
    CONSTRAINT ck_course_sections_02 CHECK (enrolled_count >= 0 AND enrolled_count <= capacity),
    CONSTRAINT ck_course_sections_03 CHECK (char_length(section_code) BETWEEN 1 AND 32),
    CONSTRAINT ck_course_sections_04 CHECK (config_version > 0),
    CONSTRAINT ck_course_sections_05 CHECK ((publication_status = 'DRAFT' AND published_at IS NULL) OR (publication_status = 'PUBLISHED' AND published_at IS NOT NULL)),
    CONSTRAINT ck_course_sections_06 CHECK (publication_status = 'PUBLISHED' OR enrolled_count = 0)
);

-- DBT-10: Course Sections.
CREATE TABLE sections.instructors (
    instructor_id uuid NOT NULL,
    display_name text NOT NULL,
    CONSTRAINT pk_sections_instructors PRIMARY KEY (instructor_id),
    CONSTRAINT ck_instructors_01 CHECK (char_length(display_name) BETWEEN 1 AND 200)
);

-- DBT-11: Course Sections.
CREATE TABLE sections.section_instructors (
    section_id uuid NOT NULL,
    instructor_id uuid NOT NULL,
    CONSTRAINT pk_sections_section_instructors PRIMARY KEY (section_id, instructor_id),
    CONSTRAINT fk_assignment_section FOREIGN KEY (section_id) REFERENCES sections.course_sections (section_id) ON UPDATE NO ACTION ON DELETE NO ACTION,
    CONSTRAINT fk_assignment_instructor FOREIGN KEY (instructor_id) REFERENCES sections.instructors (instructor_id) ON UPDATE NO ACTION ON DELETE NO ACTION
);

-- DBT-12: Course Sections.
CREATE TABLE sections.section_meetings (
    meeting_id uuid NOT NULL,
    section_id uuid NOT NULL,
    starts_at timestamptz NOT NULL,
    ends_at timestamptz NOT NULL,
    meeting_kind text NOT NULL,
    is_required boolean NOT NULL,
    venue text,
    CONSTRAINT pk_sections_section_meetings PRIMARY KEY (meeting_id),
    CONSTRAINT uq_meeting_section_interval UNIQUE (section_id, starts_at, ends_at),
    CONSTRAINT fk_meeting_section FOREIGN KEY (section_id) REFERENCES sections.course_sections (section_id) ON UPDATE NO ACTION ON DELETE NO ACTION,
    CONSTRAINT ck_section_meetings_01 CHECK (ends_at > starts_at),
    CONSTRAINT ck_section_meetings_02 CHECK (char_length(meeting_kind) BETWEEN 1 AND 32),
    CONSTRAINT ck_section_meetings_03 CHECK (venue IS NULL OR char_length(venue) BETWEEN 1 AND 200)
);

-- DBT-13: Course Catalog.
CREATE TABLE catalog.offering_prerequisites (
    offering_id uuid NOT NULL,
    required_course_id uuid NOT NULL,
    minimum_grade numeric(6,2),
    grade_scheme text,
    CONSTRAINT pk_catalog_offering_prerequisites PRIMARY KEY (offering_id, required_course_id),
    CONSTRAINT fk_prerequisite_offering FOREIGN KEY (offering_id) REFERENCES catalog.course_offerings (offering_id) ON UPDATE NO ACTION ON DELETE NO ACTION,
    CONSTRAINT fk_prerequisite_course FOREIGN KEY (required_course_id) REFERENCES catalog.courses (course_id) ON UPDATE NO ACTION ON DELETE NO ACTION,
    CONSTRAINT ck_offering_prerequisites_01 CHECK ((minimum_grade IS NULL AND grade_scheme IS NULL) OR (minimum_grade IS NOT NULL AND minimum_grade >= 0 AND grade_scheme IS NOT NULL AND char_length(grade_scheme) BETWEEN 1 AND 64))
);

-- DBT-14: Student Management.
CREATE TABLE students.eligibility_snapshots (
    snapshot_id uuid NOT NULL,
    student_id uuid NOT NULL,
    source_version text NOT NULL,
    evidence_state text NOT NULL,
    academic_status text NOT NULL,
    captured_at timestamptz NOT NULL,
    CONSTRAINT pk_students_eligibility_snapshots PRIMARY KEY (snapshot_id),
    CONSTRAINT uq_snapshot_identity_student UNIQUE (snapshot_id, student_id),
    CONSTRAINT uq_snapshot_student_source UNIQUE (student_id, source_version),
    CONSTRAINT fk_snapshot_student FOREIGN KEY (student_id) REFERENCES students.students (student_id) ON UPDATE NO ACTION ON DELETE NO ACTION,
    CONSTRAINT ck_eligibility_snapshots_01 CHECK (evidence_state IN ('COMPLETE','UNAVAILABLE')),
    CONSTRAINT ck_eligibility_snapshots_02 CHECK (academic_status IN ('ACTIVE','INACTIVE')),
    CONSTRAINT ck_eligibility_snapshots_03 CHECK (char_length(source_version) BETWEEN 1 AND 128)
);

-- DBT-15: Student Management.
CREATE TABLE students.completed_courses (
    snapshot_id uuid NOT NULL,
    course_id uuid NOT NULL,
    passed boolean NOT NULL,
    grade_value numeric(6,2),
    grade_scheme text,
    completed_on date NOT NULL,
    CONSTRAINT pk_students_completed_courses PRIMARY KEY (snapshot_id, course_id),
    CONSTRAINT fk_completion_snapshot FOREIGN KEY (snapshot_id) REFERENCES students.eligibility_snapshots (snapshot_id) ON UPDATE NO ACTION ON DELETE NO ACTION,
    CONSTRAINT fk_completion_course FOREIGN KEY (course_id) REFERENCES catalog.courses (course_id) ON UPDATE NO ACTION ON DELETE NO ACTION,
    CONSTRAINT ck_completed_courses_01 CHECK ((grade_value IS NULL AND grade_scheme IS NULL) OR (grade_value IS NOT NULL AND grade_value >= 0 AND grade_scheme IS NOT NULL AND char_length(grade_scheme) BETWEEN 1 AND 64))
);

-- DBT-16: Academic Rules.
CREATE TABLE rules.offering_policy_eligibility (
    offering_id uuid NOT NULL,
    policy_id uuid NOT NULL,
    term_id uuid NOT NULL,
    CONSTRAINT pk_rules_offering_policy_eligibility PRIMARY KEY (offering_id, policy_id),
    CONSTRAINT fk_allowed_offering_term FOREIGN KEY (offering_id, term_id) REFERENCES catalog.course_offerings (offering_id, term_id) ON UPDATE NO ACTION ON DELETE NO ACTION,
    CONSTRAINT fk_allowed_policy_term_cohort FOREIGN KEY (policy_id, term_id) REFERENCES rules.registration_policies (policy_id, term_id) ON UPDATE NO ACTION ON DELETE NO ACTION
);

-- DBT-17: Enrollment.
CREATE TABLE enrollment.student_terms (
    student_id uuid NOT NULL,
    term_id uuid NOT NULL,
    policy_id uuid NOT NULL,
    cohort_code text NOT NULL,
    eligibility_snapshot_id uuid NOT NULL,
    credit_limit_override numeric(6,2),
    created_at timestamptz NOT NULL,
    CONSTRAINT pk_enrollment_student_terms PRIMARY KEY (student_id, term_id),
    CONSTRAINT uq_student_term_policy UNIQUE (student_id, term_id, policy_id),
    CONSTRAINT fk_student_term_student FOREIGN KEY (student_id) REFERENCES students.students (student_id) ON UPDATE NO ACTION ON DELETE NO ACTION,
    CONSTRAINT fk_student_term_policy FOREIGN KEY (policy_id, term_id, cohort_code) REFERENCES rules.registration_policies (policy_id, term_id, cohort_code) ON UPDATE NO ACTION ON DELETE NO ACTION,
    CONSTRAINT fk_student_term_snapshot FOREIGN KEY (eligibility_snapshot_id, student_id) REFERENCES students.eligibility_snapshots (snapshot_id, student_id) ON UPDATE NO ACTION ON DELETE NO ACTION,
    CONSTRAINT ck_student_terms_01 CHECK (credit_limit_override IS NULL OR credit_limit_override >= 0),
    CONSTRAINT ck_student_terms_02 CHECK (char_length(cohort_code) BETWEEN 1 AND 64)
);

-- DBT-18: Enrollment.
CREATE TABLE enrollment.enrollments (
    enrollment_id uuid NOT NULL,
    student_id uuid NOT NULL,
    term_id uuid NOT NULL,
    policy_id uuid NOT NULL,
    offering_id uuid NOT NULL,
    section_id uuid NOT NULL,
    enroll_window_id uuid NOT NULL,
    status text NOT NULL,
    enrolled_at timestamptz NOT NULL,
    withdrawn_at timestamptz,
    withdraw_window_id uuid,
    CONSTRAINT pk_enrollment_enrollments PRIMARY KEY (enrollment_id),
    CONSTRAINT fk_enrollment_student_term_policy FOREIGN KEY (student_id, term_id, policy_id) REFERENCES enrollment.student_terms (student_id, term_id, policy_id) ON UPDATE NO ACTION ON DELETE NO ACTION,
    CONSTRAINT fk_enrollment_section_identity FOREIGN KEY (section_id, offering_id, term_id) REFERENCES sections.course_sections (section_id, offering_id, term_id) ON UPDATE NO ACTION ON DELETE NO ACTION,
    CONSTRAINT fk_enrollment_open_window FOREIGN KEY (enroll_window_id, policy_id) REFERENCES rules.registration_windows (window_id, policy_id) ON UPDATE NO ACTION ON DELETE NO ACTION,
    CONSTRAINT fk_enrollment_withdraw_window FOREIGN KEY (withdraw_window_id, policy_id) REFERENCES rules.registration_windows (window_id, policy_id) ON UPDATE NO ACTION ON DELETE NO ACTION,
    CONSTRAINT ck_enrollments_01 CHECK ((status = 'ACTIVE' AND withdrawn_at IS NULL AND withdraw_window_id IS NULL) OR (status = 'WITHDRAWN' AND withdrawn_at IS NOT NULL AND withdraw_window_id IS NOT NULL AND withdrawn_at >= enrolled_at))
);

-- DBT-19: Shared technical infrastructure; Enrollment/Administration own their operation semantics.
CREATE TABLE operations.idempotency_records (
    operation_id uuid NOT NULL,
    principal_id uuid NOT NULL,
    operation_type text NOT NULL,
    operation_version smallint NOT NULL,
    idempotency_key uuid NOT NULL,
    request_fingerprint bytea NOT NULL,
    target_type text NOT NULL,
    target_id uuid,
    target_term_id uuid,
    state text NOT NULL,
    created_at timestamptz NOT NULL,
    decided_at timestamptz,
    response_status smallint,
    response_code text,
    response_body jsonb,
    expires_at timestamptz,
    CONSTRAINT pk_operations_idempotency_records PRIMARY KEY (operation_id),
    CONSTRAINT uq_idempotency_scope UNIQUE (principal_id, operation_type, operation_version, idempotency_key),
    CONSTRAINT fk_idempotency_principal FOREIGN KEY (principal_id) REFERENCES identity.test_accounts (principal_id) ON UPDATE NO ACTION ON DELETE NO ACTION,
    CONSTRAINT ck_idempotency_records_01 CHECK (operation_version > 0),
    CONSTRAINT ck_idempotency_records_02 CHECK (octet_length(request_fingerprint) = 32),
    CONSTRAINT ck_idempotency_records_03 CHECK (operation_type IN ('ENROLL','WITHDRAW','ADMIN_CAPACITY_UPDATE','ADMIN_WINDOW_CLOSE','ADMIN_PREPARE_STUDENT_TERM','ADMIN_CATALOG_CHANGE','ADMIN_PUBLISH')),
    CONSTRAINT ck_idempotency_records_04 CHECK (target_type IN ('SECTION','ENROLLMENT','WINDOW','COURSE','OFFERING','STUDENT_TERM','TERM','COURSE_COLLECTION','OFFERING_COLLECTION','SECTION_COLLECTION')),
    CONSTRAINT ck_idempotency_records_05 CHECK ((target_type IN ('COURSE_COLLECTION','OFFERING_COLLECTION','SECTION_COLLECTION') AND target_id IS NULL) OR (target_type NOT IN ('COURSE_COLLECTION','OFFERING_COLLECTION','SECTION_COLLECTION') AND target_id IS NOT NULL)),
    CONSTRAINT ck_idempotency_records_06 CHECK ((target_type = 'STUDENT_TERM' AND target_term_id IS NOT NULL) OR (target_type <> 'STUDENT_TERM' AND target_term_id IS NULL)),
    CONSTRAINT ck_idempotency_records_07 CHECK ((state = 'CLAIMED' AND decided_at IS NULL AND response_status IS NULL AND response_code IS NULL AND response_body IS NULL AND expires_at IS NULL) OR (state = 'COMPLETED' AND decided_at IS NOT NULL AND response_status IS NOT NULL AND response_status BETWEEN 200 AND 499 AND response_code IS NOT NULL AND response_body IS NOT NULL AND expires_at IS NOT NULL AND expires_at > decided_at)),
    CONSTRAINT ck_idempotency_records_08 CHECK (response_body IS NULL OR jsonb_typeof(response_body) = 'object'),
    CONSTRAINT ck_idempotency_records_09 CHECK (response_code IS NULL OR char_length(response_code) BETWEEN 1 AND 64)
);

-- DBT-20: Shared technical infrastructure; originating module owns event semantics.
CREATE TABLE audit.audit_events (
    audit_id uuid NOT NULL,
    operation_id uuid NOT NULL,
    actor_principal_id uuid NOT NULL,
    event_type text NOT NULL,
    target_type text NOT NULL,
    target_id uuid NOT NULL,
    enrollment_id uuid,
    decided_at timestamptz NOT NULL,
    correlation_id uuid NOT NULL,
    details jsonb NOT NULL,
    CONSTRAINT pk_audit_audit_events PRIMARY KEY (audit_id),
    CONSTRAINT uq_audit_operation_event_target UNIQUE (operation_id, event_type, target_type, target_id),
    CONSTRAINT fk_audit_actor FOREIGN KEY (actor_principal_id) REFERENCES identity.test_accounts (principal_id) ON UPDATE NO ACTION ON DELETE NO ACTION,
    CONSTRAINT fk_audit_enrollment FOREIGN KEY (enrollment_id) REFERENCES enrollment.enrollments (enrollment_id) ON UPDATE NO ACTION ON DELETE NO ACTION,
    CONSTRAINT ck_audit_events_01 CHECK (jsonb_typeof(details) = 'object'),
    CONSTRAINT ck_audit_events_02 CHECK (char_length(event_type) BETWEEN 1 AND 64),
    CONSTRAINT ck_audit_events_03 CHECK (char_length(target_type) BETWEEN 1 AND 32)
);

-- DBT-21: Authentication.
CREATE TABLE identity.spring_session (
    primary_id char(36) NOT NULL,
    session_id char(36) NOT NULL,
    creation_time bigint NOT NULL,
    last_access_time bigint NOT NULL,
    max_inactive_interval integer NOT NULL,
    expiry_time bigint NOT NULL,
    principal_name varchar(100),
    CONSTRAINT pk_identity_spring_session PRIMARY KEY (primary_id),
    CONSTRAINT spring_session_ix1 UNIQUE (session_id)
);

-- DBT-22: Authentication.
CREATE TABLE identity.spring_session_attributes (
    session_primary_id char(36) NOT NULL,
    attribute_name varchar(200) NOT NULL,
    attribute_bytes bytea NOT NULL,
    CONSTRAINT pk_identity_spring_session_attributes PRIMARY KEY (session_primary_id, attribute_name),
    CONSTRAINT spring_session_attributes_fk FOREIGN KEY (session_primary_id) REFERENCES identity.spring_session (primary_id) ON UPDATE NO ACTION ON DELETE CASCADE
);

RESET ROLE;
