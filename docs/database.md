# PostgreSQL physical database design

Status: Phase 3 physical dictionary implemented through Phase 5, November 6, 2026. The 22 tables, 28 FKs and 50 mandatory indexes are unchanged. V1–V6 remain byte-identical; V7 adds a restricted student-term lock function only. Core Enrollment is implemented; [Phase 5 verification](phase5-verification.md) records the executed scope. Performance capacity remains unproven. Existing identifiers are preserved.

## Document ownership and scope

| Document | Authority |
| --- | --- |
| This document | Table/column/type/nullability/constraint ownership, index design and model alternatives |
| [Database ERD](database-erd.md) | Detailed Mermaid entity/relationship view, including Course → Offering → Section → Enrollment |
| [Database concurrency](database-concurrency.md) | Exact physical lock/claim/cleanup protocols and implicit FK/uniqueness effects |
| [Data integrity](data-integrity.md) | Enforcement boundaries, privilege/deletion design, synthetic fixtures, future verification, risks and Phase 4 gate |
| [Enrollment design](enrollment-design.md), [Architecture](architecture.md) | Preserved Phase 2 use-case semantics, module boundaries and decision rationale |
| [Requirements](requirements.md), [Security](security.md), [Performance](performance.md) | Required behavior, synthetic-only identity, and unverified numerical workload targets |

Physical schema namespaces reflect code ownership; they are not separate services, databases or connection pools. The same PostgreSQL root transaction crosses owning-module ports through one datasource. Shared `operations` and `audit` schemas are technical adapters; Enrollment and Administration retain their operation-specific semantics and use a shared public technical port, not each other's internal repositories.

## Model summary and conventions

- PostgreSQL is the sole authority for confirmed membership. No Redis lock, counter or session dependency is introduced.
- Core IDs are application-generated UUIDs with immutable meanings; no UUID extension or database version-specific UUID generator is required by this design. Client idempotency keys are narrowed to random UUIDs, validated before transaction entry. They are identifiers, not authentication credentials.
- Credits use exact finite `numeric(6,2)`, including zero-credit subjects if later needed. Phase 4 finding F4-01 adds explicit NaN rejection; a nonnegative comparison alone permits PostgreSQL NaN. This storage precision and its upper bound are project assumptions, not SDU academic rules; no official credit limit is invented. A student limit is the pinned policy value or an explicit pre-opening override.
- Domain operation/window/meeting instants use `timestamptz`; local academic dates use `date` plus the term's validated IANA timezone. Session framework timestamps deliberately retain epoch milliseconds. `decided_at` is not an exact commit timestamp.
- All normal PK/FK/UNIQUE/CHECK constraints are immediate and NOT DEFERRABLE unless explicitly stated. Domain FKs use NO ACTION on delete/update. Primary/foreign IDs are immutable and published academic versions are retained. The only selected cascade is session → ephemeral session attributes.
- A nullable FK is explicitly identified below. Composite nullable withdrawal-window reference uses normal MATCH SIMPLE; the status CHECK separately requires the window when withdrawn. Required UNIQUE key fields are NOT NULL; the intentionally nullable account/student mapping allows multiple admin accounts.
- Default only where stated: new `enrolled_count` is zero; other values are supplied explicitly by the owning API/fixture/framework. No PostgreSQL enum types, arrays of FKs, JSON academic rule language, partitions or extra extensions are needed initially.
- Each table declares PK, ordinary UNIQUE, FK, CHECK, secondary indexes and lifecycle. An empty list means deliberately none beyond global conventions. A PK/UNIQUE creates its backing B-tree index; do not create another identical index.

## Course, offering, section, meeting and enrollment distinctions

**Course** is the stable subject. **Offering** is that course's immutable published academic snapshot for a term, including credits/title/prerequisite definitions. Baseline UNIQUE(term_id, course_id) permits one offering per subject/term. Draft revision is editable before publication; published content is not changed in place, even after withdrawal. A future same-term replacement/versioning workflow needs separate review. **Section** is one enrollable group of the offering, with its own capacity and every required dated meeting. **Enrollment** is one student's retained membership episode in that section.

Multiple sections and teachers are supported: section/instructor assignments are many-to-many; expanded section meetings represent every required lecture/lab occurrence. Meeting kinds are descriptive. Baseline enrollment consumes one section seat and its offering's credits once, regardless of how many required meetings or teachers the section has.

Q-03 remains provisional: one active section per offering. Alternatives are (a) a bundled section containing all required lecture/lab meetings, selected here; (b) independently enrollable components plus an offering-level selection with component constraints; or (c) a bundle choice atomically allocating several section rows in sorted order. The latter two require new component/bundle relationships, credit counting and all-or-nothing multi-seat rules; the provisional offering unique index would need redesign. None is asserted to be official SDU policy.

## Academic evidence and policy choices

Prerequisites are provisionally an AND-list of completed subjects, optionally with a comparable minimum numeric grade and explicit scheme. No rows means no prerequisites. An explicit COMPLETE evidence snapshot with no completion rows is known empty history; a missing/UNAVAILABLE snapshot is not success. Published snapshots and their children freeze once assigned to a published student-term. OR rules, raw repeated attempts, equivalencies, waivers and corequisites remain Q-05 decisions.

Each student-term pins a cohort policy and eligibility snapshot. Offering/policy eligibility is an explicit allowlist, not a guessed university permission rule. The term gate is pre-provisioned independently of windows. There is one enrollment and one withdrawal interval per policy; multiple intervals, reopening and live overrides remain Q-06 decisions. Only administrative close is an online policy mutation in this baseline.

No student credit total is stored: sum credits from ACTIVE episodes and immutable offerings while holding the student-term row. This avoids another counter invariant. The section occupancy counter is retained because it is the final seat-allocation guard.

## Table catalog

| ID | Table | Owner | Purpose |
| --- | --- | --- | --- |
| DBT-01 | `students.students` | Student Management | Stable synthetic academic student identity; no university data. |
| DBT-02 | `identity.test_accounts` | Authentication | Predefined synthetic student/admin principals and credential verifiers. |
| DBT-03 | `catalog.academic_terms` | Course Catalog | Stable term identity, academic dates and explicit institutional timezone. |
| DBT-04 | `rules.term_policy_gates` | Academic Rules | One stable lockable row per term, coordinating all policy/window selection. |
| DBT-05 | `rules.registration_policies` | Academic Rules | Immutable term/cohort policy version including default credit ceiling. |
| DBT-06 | `rules.registration_windows` | Academic Rules | One enrollment and one withdrawal interval per policy version. |
| DBT-07 | `catalog.courses` | Course Catalog | Stable academic subject, separate from a term offering and a student group. |
| DBT-08 | `catalog.course_offerings` | Course Catalog | Published academic snapshot of one course in one term. |
| DBT-09 | `sections.course_sections` | Course Sections | Concrete enrollable group with transactional occupancy. |
| DBT-10 | `sections.instructors` | Course Sections | Synthetic instructor identity, allowing several teachers per group without repeated names. |
| DBT-11 | `sections.section_instructors` | Course Sections | Many-to-many group/teacher assignment; co-teaching does not require a new enrollment. |
| DBT-12 | `sections.section_meetings` | Course Sections | Expanded dated required/optional meeting occurrences for conflict checks. |
| DBT-13 | `catalog.offering_prerequisites` | Course Catalog | Conjunction of required completed subjects for one published offering. |
| DBT-14 | `students.eligibility_snapshots` | Student Management | Explicit complete/unavailable academic evidence, including students with no completed courses. |
| DBT-15 | `students.completed_courses` | Student Management | Normalized synthetic passed/failed subject evidence within one snapshot. |
| DBT-16 | `rules.offering_policy_eligibility` | Academic Rules | Explicit allowed offering/policy pairs, avoiding an implicit all-cohorts permission. |
| DBT-17 | `enrollment.student_terms` | Enrollment | Pre-provisioned student/term mutex and immutable registration-policy/evidence assignment. |
| DBT-18 | `enrollment.enrollments` | Enrollment | Retained enrollment episodes: ACTIVE to WITHDRAWN, never resurrected. |
| DBT-19 | `operations.idempotency_records` | Shared technical infrastructure; Enrollment/Administration own their operation semantics | Transactional claim and terminal replay storage shared by approved command scopes. |
| DBT-20 | `audit.audit_events` | Shared technical infrastructure; originating module owns event semantics | Append-only transactional business decisions and enrollment transition history. |
| DBT-21 | `identity.spring_session` | Authentication | Standard Spring Session JDBC session metadata, kept compatible with its PostgreSQL reference schema. |
| DBT-22 | `identity.spring_session_attributes` | Authentication | Standard framework session attributes; a separate table is justified by the selected JDBC adapter. |

## Physical table specifications

NN means NOT NULL; nullable means SQL NULL is permitted only for the stated case. All relationships are the declared FKs below; logical/audit references without FK are explicitly explained.

### DBT-01 — students.students

**Purpose / owner:** Stable synthetic academic student identity; no university data. Owner: Student Management.

| Column | PostgreSQL type | Nullability | Meaning |
| --- | --- | --- | --- |
| `student_id` | `uuid` | NN | Application-generated immutable identity. |
| `student_number` | `text` | NN | Unique synthetic identifier, not a real SDU student number. |
| `display_name` | `text` | NN | Synthetic display label. |
| `created_at` | `timestamptz` | NN | Record creation instant. |

- **Primary key:** `pk_students_students` on `(student_id)`.
- **Ordinary UNIQUE:** `uq_students_number (student_number)`
- **Foreign keys / relationships:** None.
- **CHECK constraints:** `char_length(student_number) BETWEEN 1 AND 64`; `char_length(display_name) BETWEEN 1 AND 200`
- **Indexes:** PK/ordinary-UNIQUE backing indexes; no additional index.
- **Lifecycle / enforcement:** Identity is retained. Academic status/cohort evidence used for registration is pinned separately; changing a display label does not change eligibility.

### DBT-02 — identity.test_accounts

**Purpose / owner:** Predefined synthetic student/admin principals and credential verifiers. Owner: Authentication.

| Column | PostgreSQL type | Nullability | Meaning |
| --- | --- | --- | --- |
| `principal_id` | `uuid` | NN | Immutable principal used for authorization and idempotency scope. |
| `student_id` | `uuid` | Nullable | Required for STUDENT, absent for ADMIN in the baseline. |
| `login_name` | `text` | NN | Canonical lowercase synthetic login. |
| `password_hash` | `text` | NN | Encoded adaptive test-password verifier; never plaintext or returned in DTOs. |
| `role` | `text` | NN | STUDENT or ADMIN; no self-selected role. |
| `enabled` | `boolean` | NN | Explicit account disablement; initially true for valid fixtures. |
| `auth_version` | `bigint` | NN | Positive revision for revocation/role changes. |
| `created_at` | `timestamptz` | NN | Creation instant. |

- **Primary key:** `pk_identity_test_accounts` on `(principal_id)`.
- **Ordinary UNIQUE:** `uq_test_accounts_student (student_id)`; `uq_test_accounts_login (login_name)`
- **Foreign keys / relationships:** `fk_account_student: (student_id) → students.students (student_id)`, ON DELETE NO ACTION
- **CHECK constraints:** `role IN ('STUDENT','ADMIN')`; `(role = 'STUDENT' AND student_id IS NOT NULL) OR (role = 'ADMIN' AND student_id IS NULL)`; `auth_version > 0`; `char_length(login_name) BETWEEN 1 AND 100 AND login_name = lower(login_name)`; `char_length(password_hash) BETWEEN 1 AND 1024`
- **Indexes:** PK/ordinary-UNIQUE backing indexes; no additional index.
- **Lifecycle / enforcement:** Principal/student mapping is immutable after setup. Disable/revoke using non-key columns, never delete/rekey an account online. Nullable UNIQUE permits several admin accounts with no student mapping.

### DBT-03 — catalog.academic_terms

**Purpose / owner:** Stable term identity, academic dates and explicit institutional timezone. Owner: Course Catalog.

| Column | PostgreSQL type | Nullability | Meaning |
| --- | --- | --- | --- |
| `term_id` | `uuid` | NN | Immutable term anchor. |
| `code` | `text` | NN | Unique synthetic term code. |
| `starts_on` | `date` | NN | First academic local date. |
| `ends_on` | `date` | NN | Last academic local date, inclusive. |
| `timezone_name` | `text` | NN | Validated IANA timezone name; not inferred from the developer machine. |

- **Primary key:** `pk_catalog_academic_terms` on `(term_id)`.
- **Ordinary UNIQUE:** `uq_terms_code (code)`
- **Foreign keys / relationships:** None.
- **CHECK constraints:** `ends_on >= starts_on`; `char_length(code) BETWEEN 1 AND 32`; `char_length(timezone_name) BETWEEN 1 AND 100`
- **Indexes:** PK/ordinary-UNIQUE backing indexes; no additional index.
- **Lifecycle / enforcement:** Validate timezone against supported PostgreSQL/IANA names at publication. Term dates/timezone are immutable after policy publication; a cross-table/timezone-catalog CHECK is not proposed.

### DBT-04 — rules.term_policy_gates

**Purpose / owner:** One stable lockable row per term, coordinating all policy/window selection. Owner: Academic Rules.

| Column | PostgreSQL type | Nullability | Meaning |
| --- | --- | --- | --- |
| `term_id` | `uuid` | NN | PK and FK to the immutable term. |
| `configuration_state` | `text` | NN | PREPARING or PUBLISHED. |
| `revision` | `bigint` | NN | Positive configuration revision, updated only while holding the gate exclusively. |
| `updated_at` | `timestamptz` | NN | Last configuration decision instant. |

- **Primary key:** `pk_rules_term_policy_gates` on `(term_id)`.
- **Ordinary UNIQUE:** None.
- **Foreign keys / relationships:** `fk_gate_term: (term_id) → catalog.academic_terms (term_id)`, ON DELETE NO ACTION
- **CHECK constraints:** `configuration_state IN ('PREPARING','PUBLISHED')`; `revision > 0`
- **Indexes:** PK/ordinary-UNIQUE backing indexes; no additional index.
- **Lifecycle / enforcement:** Pre-provision before opening. Enrollment/withdrawal/capacity commands use FOR SHARE; publication/window close use FOR UPDATE. A shared-lock capacity command must not update this revision or upgrade the gate.

### DBT-05 — rules.registration_policies

**Purpose / owner:** Immutable term/cohort policy version including default credit ceiling. Owner: Academic Rules.

| Column | PostgreSQL type | Nullability | Meaning |
| --- | --- | --- | --- |
| `policy_id` | `uuid` | NN | Immutable policy-version identity. |
| `term_id` | `uuid` | NN | Term anchor. |
| `cohort_code` | `text` | NN | Explicit synthetic cohort; no NULL/wildcard matching. |
| `policy_version` | `integer` | NN | Positive version within term/cohort. |
| `credit_limit` | `numeric(6,2)` | NN | Nonnegative configured credits, not an official SDU value. |
| `created_at` | `timestamptz` | NN | Creation instant. |

- **Primary key:** `pk_rules_registration_policies` on `(policy_id)`.
- **Ordinary UNIQUE:** `uq_policy_term_cohort_version (term_id, cohort_code, policy_version)`; `uq_policy_identity_term_cohort (policy_id, term_id, cohort_code)`; `uq_policy_identity_term (policy_id, term_id)`
- **Foreign keys / relationships:** `fk_policy_term: (term_id) → catalog.academic_terms (term_id)`, ON DELETE NO ACTION
- **CHECK constraints:** `policy_version > 0`; `credit_limit >= 0`; `char_length(cohort_code) BETWEEN 1 AND 64`; `credit_limit <> 'NaN'::numeric`
- **Indexes:** PK/ordinary-UNIQUE backing indexes; no additional index.
- **Lifecycle / enforcement:** Student-term assignment pins one exact policy. No implicit latest-version switch during enrollment. Published/referenced policy content is immutable.

### DBT-06 — rules.registration_windows

**Purpose / owner:** One enrollment and one withdrawal interval per policy version. Owner: Academic Rules.

| Column | PostgreSQL type | Nullability | Meaning |
| --- | --- | --- | --- |
| `window_id` | `uuid` | NN | Stable interval identity. |
| `policy_id` | `uuid` | NN | Owning policy version. |
| `operation_type` | `text` | NN | ENROLL or WITHDRAW. |
| `opens_at` | `timestamptz` | NN | Inclusive opening instant. |
| `closes_at` | `timestamptz` | NN | Exclusive natural closing instant. |
| `is_closed` | `boolean` | NN | Administrative close flag; false at publication. |
| `closed_at` | `timestamptz` | Nullable | Present exactly when administratively closed. |

- **Primary key:** `pk_rules_registration_windows` on `(window_id)`.
- **Ordinary UNIQUE:** `uq_window_policy_operation (policy_id, operation_type)`; `uq_window_identity_policy (window_id, policy_id)`
- **Foreign keys / relationships:** `fk_window_policy: (policy_id) → rules.registration_policies (policy_id)`, ON DELETE NO ACTION
- **CHECK constraints:** `operation_type IN ('ENROLL','WITHDRAW')`; `closes_at > opens_at`; `(is_closed AND closed_at IS NOT NULL) OR (NOT is_closed AND closed_at IS NULL)`
- **Indexes:** PK/ordinary-UNIQUE backing indexes; no additional index.
- **Lifecycle / enforcement:** Definitions freeze at publication; only explicit close is online baseline behavior, under exclusive term-gate protection. Reopening, multiple intervals and overrides require review.

### DBT-07 — catalog.courses

**Purpose / owner:** Stable academic subject, separate from a term offering and a student group. Owner: Course Catalog.

| Column | PostgreSQL type | Nullability | Meaning |
| --- | --- | --- | --- |
| `course_id` | `uuid` | NN | Immutable subject identity. |
| `code` | `text COLLATE "C"` | NN | Unique canonical uppercase code for exact/prefix filtering. |
| `title` | `text` | NN | Canonical subject title; offering retains its published title snapshot. |
| `created_at` | `timestamptz` | NN | Creation instant. |

- **Primary key:** `pk_catalog_courses` on `(course_id)`.
- **Ordinary UNIQUE:** `uq_courses_code (code)`
- **Foreign keys / relationships:** None.
- **CHECK constraints:** `char_length(code) BETWEEN 1 AND 32 AND code = upper(code)`; `char_length(title) BETWEEN 1 AND 240`
- **Indexes:** PK/ordinary-UNIQUE backing indexes; no additional index.
- **Lifecycle / enforcement:** Keep subject identity/code stable; course credit/prerequisite facts used for enrollment live in the immutable term offering.

### DBT-08 — catalog.course_offerings

**Purpose / owner:** Published academic snapshot of one course in one term. Owner: Course Catalog.

| Column | PostgreSQL type | Nullability | Meaning |
| --- | --- | --- | --- |
| `offering_id` | `uuid` | NN | Immutable published-snapshot identity. |
| `term_id` | `uuid` | NN | Offering term. |
| `course_id` | `uuid` | NN | Stable academic subject. |
| `title` | `text` | NN | Published title snapshot. |
| `credits` | `numeric(6,2)` | NN | Exact nonnegative credits; two decimals are a project assumption. |
| `publication_status` | `text` | NN | DRAFT or PUBLISHED. |
| `draft_revision` | `bigint` | NN | Positive optimistic revision for draft edits, frozen after publication. |
| `published_at` | `timestamptz` | Nullable | Present exactly when PUBLISHED. |

- **Primary key:** `pk_catalog_course_offerings` on `(offering_id)`.
- **Ordinary UNIQUE:** `uq_offering_term_course (term_id, course_id)`; `uq_offering_identity_term (offering_id, term_id)`
- **Foreign keys / relationships:** `fk_offering_term: (term_id) → catalog.academic_terms (term_id)`, ON DELETE NO ACTION; `fk_offering_course: (course_id) → catalog.courses (course_id)`, ON DELETE NO ACTION
- **CHECK constraints:** `credits >= 0`; `draft_revision > 0`; `char_length(title) BETWEEN 1 AND 240`; `(publication_status = 'DRAFT' AND published_at IS NULL) OR (publication_status = 'PUBLISHED' AND published_at IS NOT NULL)`; `credits <> 'NaN'::numeric`
- **Indexes:** PK/ordinary-UNIQUE backing indexes; `ix_offering_published_term (term_id, course_id)` WHERE `publication_status = 'PUBLISHED'` (optional read candidate).
- **Lifecycle / enforcement:** Baseline has one offering per course/term. Its ID is the academic snapshot version: no in-place published academic edits or second same-term revision. New versions use future terms unless a separate correction design is approved. The partial read index is conditional, not an automatic baseline addition.

### DBT-09 — sections.course_sections

**Purpose / owner:** Concrete enrollable group with transactional occupancy. Owner: Course Sections.

| Column | PostgreSQL type | Nullability | Meaning |
| --- | --- | --- | --- |
| `section_id` | `uuid` | NN | Immutable group/academic-snapshot identity. |
| `offering_id` | `uuid` | NN | Owning offering. |
| `term_id` | `uuid` | NN | Deliberately redundant term, constrained by a composite FK. |
| `section_code` | `text` | NN | Unique within offering and term. |
| `capacity` | `integer` | NN | Nonnegative number of seats. |
| `enrolled_count` | `integer` | NN | Default 0; counts committed ACTIVE enrollment episodes under the protocol. |
| `publication_status` | `text` | NN | DRAFT or PUBLISHED. |
| `published_at` | `timestamptz` | Nullable | Present exactly when published. |
| `config_version` | `bigint` | NN | Positive admin configuration revision. Seat increments/releases do not change this version. |

- **Primary key:** `pk_sections_course_sections` on `(section_id)`.
- **Ordinary UNIQUE:** `uq_section_term_offering_code (term_id, offering_id, section_code)`; `uq_section_identity_offering_term (section_id, offering_id, term_id)`
- **Foreign keys / relationships:** `fk_section_offering_term: (offering_id, term_id) → catalog.course_offerings (offering_id, term_id)`, ON DELETE NO ACTION
- **CHECK constraints:** `capacity >= 0`; `enrolled_count >= 0 AND enrolled_count <= capacity`; `char_length(section_code) BETWEEN 1 AND 32`; `config_version > 0`; `(publication_status = 'DRAFT' AND published_at IS NULL) OR (publication_status = 'PUBLISHED' AND published_at IS NOT NULL)`; `publication_status = 'PUBLISHED' OR enrolled_count = 0`
- **Indexes:** PK/ordinary-UNIQUE backing indexes; no additional index.
- **Lifecycle / enforcement:** Published meetings/offering mapping are immutable. Only capacity/config_version and enrolled_count are online mutable through their owning APIs. No count/capacity indexes are added.

### DBT-10 — sections.instructors

**Purpose / owner:** Synthetic instructor identity, allowing several teachers per group without repeated names. Owner: Course Sections.

| Column | PostgreSQL type | Nullability | Meaning |
| --- | --- | --- | --- |
| `instructor_id` | `uuid` | NN | Stable synthetic identity. |
| `display_name` | `text` | NN | Synthetic teacher label. |

- **Primary key:** `pk_sections_instructors` on `(instructor_id)`.
- **Ordinary UNIQUE:** None.
- **Foreign keys / relationships:** None.
- **CHECK constraints:** `char_length(display_name) BETWEEN 1 AND 200`
- **Indexes:** PK/ordinary-UNIQUE backing indexes; no additional index.
- **Lifecycle / enforcement:** Synthetic data only; no real staff identity import.

### DBT-11 — sections.section_instructors

**Purpose / owner:** Many-to-many group/teacher assignment; co-teaching does not require a new enrollment. Owner: Course Sections.

| Column | PostgreSQL type | Nullability | Meaning |
| --- | --- | --- | --- |
| `section_id` | `uuid` | NN | Assigned section. |
| `instructor_id` | `uuid` | NN | Assigned teacher. |

- **Primary key:** `pk_sections_section_instructors` on `(section_id, instructor_id)`.
- **Ordinary UNIQUE:** None.
- **Foreign keys / relationships:** `fk_assignment_section: (section_id) → sections.course_sections (section_id)`, ON DELETE NO ACTION; `fk_assignment_instructor: (instructor_id) → sections.instructors (instructor_id)`, ON DELETE NO ACTION
- **CHECK constraints:** None beyond type/nullability/framework behavior.
- **Indexes:** PK/ordinary-UNIQUE backing indexes; no additional index.
- **Lifecycle / enforcement:** Assignment freezes with section publication. Reverse instructor search is not required; no reverse-only index is added without a query need.

### DBT-12 — sections.section_meetings

**Purpose / owner:** Expanded dated required/optional meeting occurrences for conflict checks. Owner: Course Sections.

| Column | PostgreSQL type | Nullability | Meaning |
| --- | --- | --- | --- |
| `meeting_id` | `uuid` | NN | Immutable occurrence identity. |
| `section_id` | `uuid` | NN | Owning group. |
| `starts_at` | `timestamptz` | NN | Inclusive actual occurrence instant. |
| `ends_at` | `timestamptz` | NN | Exclusive actual occurrence instant. |
| `meeting_kind` | `text` | NN | Descriptive label, e.g. lecture/lab; not a separately enrollable component. |
| `is_required` | `boolean` | NN | Required occurrences participate in conflict checks. |
| `venue` | `text` | Nullable | Optional synthetic room/online label. |

- **Primary key:** `pk_sections_section_meetings` on `(meeting_id)`.
- **Ordinary UNIQUE:** `uq_meeting_section_interval (section_id, starts_at, ends_at)`
- **Foreign keys / relationships:** `fk_meeting_section: (section_id) → sections.course_sections (section_id)`, ON DELETE NO ACTION
- **CHECK constraints:** `ends_at > starts_at`; `char_length(meeting_kind) BETWEEN 1 AND 32`; `venue IS NULL OR char_length(venue) BETWEEN 1 AND 200`
- **Indexes:** PK/ordinary-UNIQUE backing indexes; no additional index.
- **Lifecycle / enforcement:** Expand recurrence before publication; validate local date bounds/timezone/DST and all required meetings. No global overlap exclusion: different sections/students may meet at the same time.

### DBT-13 — catalog.offering_prerequisites

**Purpose / owner:** Conjunction of required completed subjects for one published offering. Owner: Course Catalog.

| Column | PostgreSQL type | Nullability | Meaning |
| --- | --- | --- | --- |
| `offering_id` | `uuid` | NN | Dependent offering snapshot. |
| `required_course_id` | `uuid` | NN | Stable completed subject required by that offering. |
| `minimum_grade` | `numeric(6,2)` | Nullable | Optional minimum within the named comparable grade scheme. |
| `grade_scheme` | `text` | Nullable | Required exactly when minimum_grade is present. |

- **Primary key:** `pk_catalog_offering_prerequisites` on `(offering_id, required_course_id)`.
- **Ordinary UNIQUE:** None.
- **Foreign keys / relationships:** `fk_prerequisite_offering: (offering_id) → catalog.course_offerings (offering_id)`, ON DELETE NO ACTION; `fk_prerequisite_course: (required_course_id) → catalog.courses (course_id)`, ON DELETE NO ACTION
- **CHECK constraints:** `(minimum_grade IS NULL AND grade_scheme IS NULL) OR (minimum_grade IS NOT NULL AND minimum_grade >= 0 AND grade_scheme IS NOT NULL AND char_length(grade_scheme) BETWEEN 1 AND 64)`; `minimum_grade IS NULL OR minimum_grade <> 'NaN'::numeric`
- **Indexes:** PK/ordinary-UNIQUE backing indexes; no additional index.
- **Lifecycle / enforcement:** Freeze at publication. No rows means no prerequisites. AND-only completed-course rules are provisional; cycles, self-reference and grade comparability are publication checks, not cross-table CHECKs. OR groups/corequisites/waivers need a model review.

### DBT-14 — students.eligibility_snapshots

**Purpose / owner:** Explicit complete/unavailable academic evidence, including students with no completed courses. Owner: Student Management.

| Column | PostgreSQL type | Nullability | Meaning |
| --- | --- | --- | --- |
| `snapshot_id` | `uuid` | NN | Immutable evidence version. |
| `student_id` | `uuid` | NN | Owner. |
| `source_version` | `text` | NN | Synthetic import/version provenance. |
| `evidence_state` | `text` | NN | COMPLETE or UNAVAILABLE. |
| `academic_status` | `text` | NN | ACTIVE or INACTIVE synthetic eligibility status. |
| `captured_at` | `timestamptz` | NN | Evidence capture instant. |

- **Primary key:** `pk_students_eligibility_snapshots` on `(snapshot_id)`.
- **Ordinary UNIQUE:** `uq_snapshot_identity_student (snapshot_id, student_id)`; `uq_snapshot_student_source (student_id, source_version)`
- **Foreign keys / relationships:** `fk_snapshot_student: (student_id) → students.students (student_id)`, ON DELETE NO ACTION
- **CHECK constraints:** `evidence_state IN ('COMPLETE','UNAVAILABLE')`; `academic_status IN ('ACTIVE','INACTIVE')`; `char_length(source_version) BETWEEN 1 AND 128`
- **Indexes:** PK/ordinary-UNIQUE backing indexes; no additional index.
- **Lifecycle / enforcement:** Once assigned to a published student-term, header and completed-course children are immutable. Missing/UNAVAILABLE evidence is a technical eligibility-unavailable outcome; COMPLETE with no children is known empty completion history.

### DBT-15 — students.completed_courses

**Purpose / owner:** Normalized synthetic passed/failed subject evidence within one snapshot. Owner: Student Management.

| Column | PostgreSQL type | Nullability | Meaning |
| --- | --- | --- | --- |
| `snapshot_id` | `uuid` | NN | Owning evidence version. |
| `course_id` | `uuid` | NN | Completed/attempted stable subject. |
| `passed` | `boolean` | NN | Explicit completion pass result. |
| `grade_value` | `numeric(6,2)` | Nullable | Optional comparable numeric result; no official SDU grade scale assumed. |
| `grade_scheme` | `text` | Nullable | Present exactly with grade_value. |
| `completed_on` | `date` | NN | Synthetic evidence date. |

- **Primary key:** `pk_students_completed_courses` on `(snapshot_id, course_id)`.
- **Ordinary UNIQUE:** None.
- **Foreign keys / relationships:** `fk_completion_snapshot: (snapshot_id) → students.eligibility_snapshots (snapshot_id)`, ON DELETE NO ACTION; `fk_completion_course: (course_id) → catalog.courses (course_id)`, ON DELETE NO ACTION
- **CHECK constraints:** `(grade_value IS NULL AND grade_scheme IS NULL) OR (grade_value IS NOT NULL AND grade_value >= 0 AND grade_scheme IS NOT NULL AND char_length(grade_scheme) BETWEEN 1 AND 64)`; `grade_value IS NULL OR grade_value <> 'NaN'::numeric`
- **Indexes:** PK/ordinary-UNIQUE backing indexes; no additional index.
- **Lifecycle / enforcement:** One normalized result per subject per evidence snapshot; raw repeated attempts/equivalencies are outside the baseline. Publication must define normalization; missing required grade/scheme is not assumed to pass.

### DBT-16 — rules.offering_policy_eligibility

**Purpose / owner:** Explicit allowed offering/policy pairs, avoiding an implicit all-cohorts permission. Owner: Academic Rules.

| Column | PostgreSQL type | Nullability | Meaning |
| --- | --- | --- | --- |
| `offering_id` | `uuid` | NN | Allowed offering. |
| `policy_id` | `uuid` | NN | Allowed cohort policy version. |
| `term_id` | `uuid` | NN | Same-term integrity anchor for both parents. |

- **Primary key:** `pk_rules_offering_policy_eligibility` on `(offering_id, policy_id)`.
- **Ordinary UNIQUE:** None.
- **Foreign keys / relationships:** `fk_allowed_offering_term: (offering_id, term_id) → catalog.course_offerings (offering_id, term_id)`, ON DELETE NO ACTION; `fk_allowed_policy_term_cohort: (policy_id, term_id) → rules.registration_policies (policy_id, term_id)`, ON DELETE NO ACTION
- **CHECK constraints:** None beyond type/nullability/framework behavior.
- **Indexes:** PK/ordinary-UNIQUE backing indexes; no additional index.
- **Lifecycle / enforcement:** Frozen before opening. Absence means explicitly ineligible, not an invented university policy. Policy identity/term reference support is declared below.

### DBT-17 — enrollment.student_terms

**Purpose / owner:** Pre-provisioned student/term mutex and immutable registration-policy/evidence assignment. Owner: Enrollment.

| Column | PostgreSQL type | Nullability | Meaning |
| --- | --- | --- | --- |
| `student_id` | `uuid` | NN | Student coordination identity. |
| `term_id` | `uuid` | NN | Term coordination identity. |
| `policy_id` | `uuid` | NN | Pinned policy version. |
| `cohort_code` | `text` | NN | Pinned cohort matching that policy. |
| `eligibility_snapshot_id` | `uuid` | NN | Pinned evidence belonging to the same student. |
| `credit_limit_override` | `numeric(6,2)` | Nullable | Optional authorized synthetic override; otherwise use policy credit_limit. |
| `created_at` | `timestamptz` | NN | Provisioning instant. |

- **Primary key:** `pk_enrollment_student_terms` on `(student_id, term_id)`.
- **Ordinary UNIQUE:** `uq_student_term_policy (student_id, term_id, policy_id)`
- **Foreign keys / relationships:** `fk_student_term_student: (student_id) → students.students (student_id)`, ON DELETE NO ACTION; `fk_student_term_policy: (policy_id, term_id, cohort_code) → rules.registration_policies (policy_id, term_id, cohort_code)`, ON DELETE NO ACTION; `fk_student_term_snapshot: (eligibility_snapshot_id, student_id) → students.eligibility_snapshots (snapshot_id, student_id)`, ON DELETE NO ACTION
- **CHECK constraints:** `credit_limit_override IS NULL OR credit_limit_override >= 0`; `char_length(cohort_code) BETWEEN 1 AND 64`; `credit_limit_override IS NULL OR credit_limit_override <> 'NaN'::numeric`
- **Indexes:** PK/ordinary-UNIQUE backing indexes; no additional index.
- **Lifecycle / enforcement:** Provision only under the exclusive PREPARING gate, through Enrollment public setup API. Assignments/overrides freeze at publication. No maintained student credit counter: SUM immutable offering credits for ACTIVE episodes under this row lock.

### DBT-18 — enrollment.enrollments

**Purpose / owner:** Retained enrollment episodes: ACTIVE to WITHDRAWN, never resurrected. Owner: Enrollment.

| Column | PostgreSQL type | Nullability | Meaning |
| --- | --- | --- | --- |
| `enrollment_id` | `uuid` | NN | Immutable episode identity; a new intention after withdrawal gets a new ID. |
| `student_id` | `uuid` | NN | Owner. |
| `term_id` | `uuid` | NN | Term pinned by section and student-term FKs. |
| `policy_id` | `uuid` | NN | Policy pinned by student-term and window FKs. |
| `offering_id` | `uuid` | NN | Offering pinned by the section composite FK. |
| `section_id` | `uuid` | NN | Allocated group. |
| `enroll_window_id` | `uuid` | NN | Window used for initial decision; operation type validated in protocol. |
| `status` | `text` | NN | ACTIVE or WITHDRAWN; no pending reservation state. |
| `enrolled_at` | `timestamptz` | NN | Protected decision time, not exact commit time. |
| `withdrawn_at` | `timestamptz` | Nullable | Present only after withdrawal. |
| `withdraw_window_id` | `uuid` | Nullable | Present only after withdrawal; same pinned policy. |

- **Primary key:** `pk_enrollment_enrollments` on `(enrollment_id)`.
- **Ordinary UNIQUE:** None.
- **Foreign keys / relationships:** `fk_enrollment_student_term_policy: (student_id, term_id, policy_id) → enrollment.student_terms (student_id, term_id, policy_id)`, ON DELETE NO ACTION; `fk_enrollment_section_identity: (section_id, offering_id, term_id) → sections.course_sections (section_id, offering_id, term_id)`, ON DELETE NO ACTION; `fk_enrollment_open_window: (enroll_window_id, policy_id) → rules.registration_windows (window_id, policy_id)`, ON DELETE NO ACTION; `fk_enrollment_withdraw_window: (withdraw_window_id, policy_id) → rules.registration_windows (window_id, policy_id)`, ON DELETE NO ACTION
- **CHECK constraints:** `(status = 'ACTIVE' AND withdrawn_at IS NULL AND withdraw_window_id IS NULL) OR (status = 'WITHDRAWN' AND withdrawn_at IS NOT NULL AND withdraw_window_id IS NOT NULL AND withdrawn_at >= enrolled_at)`
- **Indexes:** PK/ordinary-UNIQUE backing indexes; `ux_enrollment_active_section_student (section_id, student_id)` WHERE `status = 'ACTIVE'`; `ux_enrollment_active_student_offering (student_id, offering_id)` WHERE `status = 'ACTIVE'`; `ix_enrollment_student_term_history (student_id, term_id, enrolled_at DESC, enrollment_id)`.
- **Lifecycle / enforcement:** The selected row-local BEFORE UPDATE episode guard preserves immutable identity and permits only ACTIVE to WITHDRAWN; the protocol/column privileges still control seat release. New episode after withdrawal is allowed by partial uniqueness. Audit stores transitions; no redundant enrollment_history table. Active-only indexes are not FK targets.

### DBT-19 — operations.idempotency_records

**Purpose / owner:** Transactional claim and terminal replay storage shared by approved command scopes. Owner: Shared technical infrastructure; Enrollment/Administration own their operation semantics.

| Column | PostgreSQL type | Nullability | Meaning |
| --- | --- | --- | --- |
| `operation_id` | `uuid` | NN | Immutable attempt/intention instance identity, replaced only when an expired scope is reused. |
| `principal_id` | `uuid` | NN | Authenticated internal principal. |
| `operation_type` | `text` | NN | ENROLL, WITHDRAW, ADMIN_CAPACITY_UPDATE, ADMIN_WINDOW_CLOSE, ADMIN_PREPARE_STUDENT_TERM, ADMIN_CATALOG_CHANGE or ADMIN_PUBLISH. |
| `operation_version` | `smallint` | NN | Positive command contract version. |
| `idempotency_key` | `uuid` | NN | Opaque client-generated random UUID, never an authorization credential. |
| `request_fingerprint` | `bytea` | NN | 32-byte SHA-256 of canonical version/target/body; excludes sessions and correlation metadata. |
| `target_type` | `text` | NN | Typed resource or collection category; intentionally no domain FK at claim time. |
| `target_id` | `uuid` | Nullable | Required existing-resource ID, including missing-resource denials; NULL only for typed collection-creation targets. |
| `target_term_id` | `uuid` | Nullable | Required for composite STUDENT_TERM targets, otherwise absent; no early domain FK. |
| `state` | `text` | NN | CLAIMED only while uncommitted, or COMPLETED. |
| `created_at` | `timestamptz` | NN | Claim creation instant. |
| `decided_at` | `timestamptz` | Nullable | Required terminal protected decision time. |
| `response_status` | `smallint` | Nullable | Terminal HTTP status, required only on completion. |
| `response_code` | `text` | Nullable | Stable safe terminal outcome code. |
| `response_body` | `jsonb` | Nullable | Minimal object; no credential/transcript/raw request dump. |
| `expires_at` | `timestamptz` | Nullable | Required on completion, later than decided_at. |

- **Primary key:** `pk_operations_idempotency_records` on `(operation_id)`.
- **Ordinary UNIQUE:** `uq_idempotency_scope (principal_id, operation_type, operation_version, idempotency_key)`
- **Foreign keys / relationships:** `fk_idempotency_principal: (principal_id) → identity.test_accounts (principal_id)`, ON DELETE NO ACTION
- **CHECK constraints:** `operation_version > 0`; `octet_length(request_fingerprint) = 32`; `operation_type IN ('ENROLL','WITHDRAW','ADMIN_CAPACITY_UPDATE','ADMIN_WINDOW_CLOSE','ADMIN_PREPARE_STUDENT_TERM','ADMIN_CATALOG_CHANGE','ADMIN_PUBLISH')`; `target_type IN ('SECTION','ENROLLMENT','WINDOW','COURSE','OFFERING','STUDENT_TERM','TERM','COURSE_COLLECTION','OFFERING_COLLECTION','SECTION_COLLECTION')`; `(target_type IN ('COURSE_COLLECTION','OFFERING_COLLECTION','SECTION_COLLECTION') AND target_id IS NULL) OR (target_type NOT IN ('COURSE_COLLECTION','OFFERING_COLLECTION','SECTION_COLLECTION') AND target_id IS NOT NULL)`; `(target_type = 'STUDENT_TERM' AND target_term_id IS NOT NULL) OR (target_type <> 'STUDENT_TERM' AND target_term_id IS NULL)`; `(state = 'CLAIMED' AND decided_at IS NULL AND response_status IS NULL AND response_code IS NULL AND response_body IS NULL AND expires_at IS NULL) OR (state = 'COMPLETED' AND decided_at IS NOT NULL AND response_status IS NOT NULL AND response_status BETWEEN 200 AND 499 AND response_code IS NOT NULL AND response_body IS NOT NULL AND expires_at IS NOT NULL AND expires_at > decided_at)`; `response_body IS NULL OR jsonb_typeof(response_body) = 'object'`; `response_code IS NULL OR char_length(response_code) BETWEEN 1 AND 64`
- **Indexes:** PK/ordinary-UNIQUE backing indexes; `ix_idempotency_expiry (expires_at, operation_id)` WHERE `state = 'COMPLETED'`.
- **Lifecycle / enforcement:** A selected deferred constraint-trigger contract rejects a claim still CLAIMED at commit; inspect the final row, not the insertion NEW image. A separate row-local transition guard makes scope/fingerprint/target immutable and permits only CLAIMED to COMPLETED. Neither acquires domain locks, repairs counters or commits. Transient 429/REQUEST_IN_PROGRESS/technical responses are not terminal cache entries even though status range alone cannot express the allowed semantic set. No enrollment/audit FK points here, allowing independent expiry.

### DBT-20 — audit.audit_events

**Purpose / owner:** Append-only transactional business decisions and enrollment transition history. Owner: Shared technical infrastructure; originating module owns event semantics.

| Column | PostgreSQL type | Nullability | Meaning |
| --- | --- | --- | --- |
| `audit_id` | `uuid` | NN | Immutable event identity. |
| `operation_id` | `uuid` | NN | Stable correlation with the intention; deliberately not an FK to expiring idempotency storage. |
| `actor_principal_id` | `uuid` | NN | Authenticated synthetic actor. |
| `event_type` | `text` | NN | ENROLLED, WITHDRAWN, ENROLLMENT_DENIED or an approved admin event code. |
| `target_type` | `text` | NN | Typed business target category. |
| `target_id` | `uuid` | NN | Stable target identity, retained even for denied/missing-resource requests. |
| `enrollment_id` | `uuid` | Nullable | Actual episode for membership transitions; nullable for denials/admin changes. |
| `decided_at` | `timestamptz` | NN | Decision instant. |
| `correlation_id` | `uuid` | NN | Safe diagnostic request correlation. |
| `details` | `jsonb` | NN | Minimal allowlisted before/after/result fields, never raw credentials/transcript. |

- **Primary key:** `pk_audit_audit_events` on `(audit_id)`.
- **Ordinary UNIQUE:** `uq_audit_operation_event_target (operation_id, event_type, target_type, target_id)`
- **Foreign keys / relationships:** `fk_audit_actor: (actor_principal_id) → identity.test_accounts (principal_id)`, ON DELETE NO ACTION; `fk_audit_enrollment: (enrollment_id) → enrollment.enrollments (enrollment_id)`, ON DELETE NO ACTION
- **CHECK constraints:** `jsonb_typeof(details) = 'object'`; `char_length(event_type) BETWEEN 1 AND 64`; `char_length(target_type) BETWEEN 1 AND 32`
- **Indexes:** PK/ordinary-UNIQUE backing indexes; `ix_audit_enrollment_time (enrollment_id, decided_at, audit_id)` WHERE `enrollment_id IS NOT NULL`.
- **Lifecycle / enforcement:** INSERT only for runtime; no replay-generated second academic event. Domain membership/history is retained. Generic target and operation identities are immutable audit facts, not unsafe cascading links.

### DBT-21 — identity.spring_session

**Purpose / owner:** Standard Spring Session JDBC session metadata, kept compatible with its PostgreSQL reference schema. Owner: Authentication.

| Column | PostgreSQL type | Nullability | Meaning |
| --- | --- | --- | --- |
| `primary_id` | `char(36)` | NN | Framework-generated stable internal session-row ID. |
| `session_id` | `char(36)` | NN | Unique rotating opaque session identifier; sensitive session material. |
| `creation_time` | `bigint` | NN | Framework epoch milliseconds, intentionally not timestamptz. |
| `last_access_time` | `bigint` | NN | Framework epoch milliseconds. |
| `max_inactive_interval` | `integer` | NN | Framework seconds; configure a finite positive value in the MVP. |
| `expiry_time` | `bigint` | NN | Framework epoch-millisecond idle expiration. |
| `principal_name` | `varchar(100)` | Nullable | Nullable for pre-auth/CSRF sessions; authenticated sessions use canonical principal UUID text. |

- **Primary key:** `pk_identity_spring_session` on `(primary_id)`.
- **Ordinary UNIQUE:** `spring_session_ix1 (session_id)`
- **Foreign keys / relationships:** None.
- **CHECK constraints:** None beyond type/nullability/framework behavior.
- **Indexes:** PK/ordinary-UNIQUE backing indexes; `spring_session_ix2 (expiry_time)`; `spring_session_ix3 (principal_name)`.
- **Lifecycle / enforcement:** Retain standard schema semantics, with no additional CHECK/FK to account identity. principal_name is a framework index hint, not authorization proof. Exact adapter version/table-name customization must match the reviewed upstream schema before implementation. Absolute lifetime and auth_version are checked by the security layer.

### DBT-22 — identity.spring_session_attributes

**Purpose / owner:** Standard framework session attributes; a separate table is justified by the selected JDBC adapter. Owner: Authentication.

| Column | PostgreSQL type | Nullability | Meaning |
| --- | --- | --- | --- |
| `session_primary_id` | `char(36)` | NN | Owning internal session row. |
| `attribute_name` | `varchar(200)` | NN | Allowlisted framework authentication/CSRF attribute name. |
| `attribute_bytes` | `bytea` | NN | Restricted serialization of minimal session state; never store test password hashes/transcripts. |

- **Primary key:** `pk_identity_spring_session_attributes` on `(session_primary_id, attribute_name)`.
- **Ordinary UNIQUE:** None.
- **Foreign keys / relationships:** `spring_session_attributes_fk: (session_primary_id) → identity.spring_session (primary_id)`, ON DELETE CASCADE
- **CHECK constraints:** None beyond type/nullability/framework behavior.
- **Indexes:** PK/ordinary-UNIQUE backing indexes; no additional index.
- **Lifecycle / enforcement:** The only selected ON DELETE CASCADE: isolated ephemeral session attributes, never academic state. Framework refresh/logout/cleanup transactions remain outside domain transactions. Restrict session-state deserialization and attributes before use.

## Critical enrollment constraints and illustrative SQL

These are isolated design fragments inside documentation, not complete migrations or independently runnable use cases. Reference/table creation order and complete lifecycle guards must be reviewed before implementation.

```sql
-- Row-local section bound only; it does not count another table.
CHECK (capacity >= 0 AND enrolled_count >= 0 AND enrolled_count <= capacity)

-- Immediate active-only uniqueness; these are indexes, not FK targets.
CREATE UNIQUE INDEX ux_enrollment_active_section_student
    ON enrollment.enrollments (section_id, student_id)
    WHERE status = 'ACTIVE';

-- Provisional Q-03 choice; revisit if components/bundles are approved.
CREATE UNIQUE INDEX ux_enrollment_active_student_offering
    ON enrollment.enrollments (student_id, offering_id)
    WHERE status = 'ACTIVE';

-- Composite relationship prevents a section/offering/term mismatch.
FOREIGN KEY (section_id, offering_id, term_id)
    REFERENCES sections.course_sections (section_id, offering_id, term_id)
```

The parent has an ordinary nonpartial UNIQUE on the referenced composite tuple. Audit references the stable enrollment PK, never an active-only index. A partial active index cannot serve as the target of the FKs used here. PostgreSQL describes these distinctions and cross-table CHECK limitations in [Constraints](https://www.postgresql.org/docs/current/ddl-constraints.html).

The row bound `0 <= enrolled_count <= capacity` is directly enforced. Equality with COUNT(ACTIVE episodes) is **not** a CHECK: it is maintained by ordered seat/membership mutation in one root transaction and tested independently. There is no aggregate-count trigger in this baseline and no double accounting through both triggers and application writes. See [Database concurrency](database-concurrency.md) and [Data integrity](data-integrity.md).

Select a narrow deferred constraint-trigger contract for idempotency only: on claim insert/completion update, inspect that operation's final row at transaction end and reject a remaining CLAIMED row. It must query the final row, not reject the stale insertion NEW image. It acquires no domain locks and does not repair counters or commit. The [V4 migration](../backend/src/main/resources/db/migration/V4__enrollment_and_idempotency_guards.sql) implements the trigger; PostgreSQL supports deferred AFTER ROW constraint triggers in [CREATE TRIGGER](https://www.postgresql.org/docs/current/sql-createtrigger.html).

## Index design and query validation

The following registry includes every PK/ordinary-UNIQUE backing index and proposed secondary index. Integrity indexes are justified by keys/FKs even when they are not read optimizations. `ix_offering_published_term` is an **optional** measured candidate; do not include it automatically because the term/course ordinary UNIQUE may already be sufficient.

Each key order places the equality prefix needed by its named query first. Referenced composite keys deliberately contain redundant identity columns to enforce cross-table consistency; their index/storage cost is explicit. All indexes cost storage/WAL and maintenance on relevant INSERT/UPDATE/DELETE; no indexed seat counter or JSON GIN index is proposed.

| Index | Key / predicate | Query or integrity reason and column order | Write cost | Future EXPLAIN validation |
| --- | --- | --- | --- | --- |
| `pk_students_students` | `(student_id)` | Student profile lookup by UUID; parent identity checks. | Insert entry and uniqueness probe; key changes rewrite entries. Retained immutable keys reduce update churn. | On representative fixtures, compare actual/estimated rows, scan/filter work and buffers for the named equality/list query; keep required integrity index regardless of planner choice. |
| `uq_students_number` | `(student_number)` | Exact synthetic student-number lookup and uniqueness. | Insert entry and uniqueness probe; key changes rewrite entries. Retained immutable keys reduce update churn. | On representative fixtures, compare actual/estimated rows, scan/filter work and buffers for the named equality/list query; keep required integrity index regardless of planner choice. |
| `pk_identity_test_accounts` | `(principal_id)` | Authenticate/revoke/authorize by immutable principal UUID; idempotency/audit FK anchor. | Insert entry and uniqueness probe; key changes rewrite entries. Retained immutable keys reduce update churn. | On representative fixtures, compare actual/estimated rows, scan/filter work and buffers for the named equality/list query; keep required integrity index regardless of planner choice. |
| `uq_test_accounts_student` | `(student_id)` | Map one student to at most one test account; nullable admin mappings are intentional. | Insert entry and uniqueness probe; key changes rewrite entries. Retained immutable keys reduce update churn. | On representative fixtures, compare actual/estimated rows, scan/filter work and buffers for the named equality/list query; keep required integrity index regardless of planner choice. |
| `uq_test_accounts_login` | `(login_name)` | Exact canonical login verification; one login cannot map to two principals. | Insert entry and uniqueness probe; key changes rewrite entries. Retained immutable keys reduce update churn. | On representative fixtures, compare actual/estimated rows, scan/filter work and buffers for the named equality/list query; keep required integrity index regardless of planner choice. |
| `pk_catalog_academic_terms` | `(term_id)` | Term details by immutable ID and term parent checks. | Insert entry and uniqueness probe; key changes rewrite entries. Retained immutable keys reduce update churn. | On representative fixtures, compare actual/estimated rows, scan/filter work and buffers for the named equality/list query; keep required integrity index regardless of planner choice. |
| `uq_terms_code` | `(code)` | Exact synthetic term-code lookup. | Insert entry and uniqueness probe; key changes rewrite entries. Retained immutable keys reduce update churn. | On representative fixtures, compare actual/estimated rows, scan/filter work and buffers for the named equality/list query; keep required integrity index regardless of planner choice. |
| `pk_rules_term_policy_gates` | `(term_id)` | Point lookup FOR SHARE/FOR UPDATE by term; no section/student tuple in this index. | Insert entry and uniqueness probe; key changes rewrite entries. Retained immutable keys reduce update churn. | On representative fixtures, compare actual/estimated rows, scan/filter work and buffers for the named equality/list query; keep required integrity index regardless of planner choice. |
| `pk_rules_registration_policies` | `(policy_id)` | Pinned policy lookup by policy ID. | Insert entry and uniqueness probe; key changes rewrite entries. Retained immutable keys reduce update churn. | On representative fixtures, compare actual/estimated rows, scan/filter work and buffers for the named equality/list query; keep required integrity index regardless of planner choice. |
| `uq_policy_term_cohort_version` | `(term_id, cohort_code, policy_version)` | Resolve an exact term/cohort/version during setup; equality term/cohort before version. | Insert entry and uniqueness probe; key changes rewrite entries. Retained immutable keys reduce update churn. | On representative fixtures, compare actual/estimated rows, scan/filter work and buffers for the named equality/list query; keep required integrity index regardless of planner choice. |
| `uq_policy_identity_term_cohort` | `(policy_id, term_id, cohort_code)` | Integrity target for student-term policy/term/cohort consistency; identity prefix for FK checks. | Insert entry and uniqueness probe; key changes rewrite entries. Retained immutable keys reduce update churn. | On representative fixtures, compare actual/estimated rows, scan/filter work and buffers for the named equality/list query; keep required integrity index regardless of planner choice. |
| `uq_policy_identity_term` | `(policy_id, term_id)` | Integrity target for same-term offering/policy allowlist; a triple UNIQUE cannot be used as this pair target. | Insert entry and uniqueness probe; key changes rewrite entries. Retained immutable keys reduce update churn. | On representative fixtures, compare actual/estimated rows, scan/filter work and buffers for the named equality/list query; keep required integrity index regardless of planner choice. |
| `pk_rules_registration_windows` | `(window_id)` | Window lookup by ID for close/parent checks. | Insert entry and uniqueness probe; key changes rewrite entries. Retained immutable keys reduce update churn. | On representative fixtures, compare actual/estimated rows, scan/filter work and buffers for the named equality/list query; keep required integrity index regardless of planner choice. |
| `uq_window_policy_operation` | `(policy_id, operation_type)` | Select the sole ENROLL/WITHDRAW interval for a pinned policy; policy equality first. | Insert entry and uniqueness probe; key changes rewrite entries. Retained immutable keys reduce update churn. | On representative fixtures, compare actual/estimated rows, scan/filter work and buffers for the named equality/list query; keep required integrity index regardless of planner choice. |
| `uq_window_identity_policy` | `(window_id, policy_id)` | Integrity target for episode/window policy consistency, independent of window operation validation. | Insert entry and uniqueness probe; key changes rewrite entries. Retained immutable keys reduce update churn. | On representative fixtures, compare actual/estimated rows, scan/filter work and buffers for the named equality/list query; keep required integrity index regardless of planner choice. |
| `pk_catalog_courses` | `(course_id)` | Stable subject lookup and prerequisite/completion FK checks. | Insert entry and uniqueness probe; key changes rewrite entries. Retained immutable keys reduce update churn. | On representative fixtures, compare actual/estimated rows, scan/filter work and buffers for the named equality/list query; keep required integrity index regardless of planner choice. |
| `uq_courses_code` | `(code)` | Exact/prefix canonical course-code filtering; C collation permits predictable byte ordering. | Insert entry and uniqueness probe; key changes rewrite entries. Retained immutable keys reduce update churn. | On representative fixtures, compare actual/estimated rows, scan/filter work and buffers for the named equality/list query; keep required integrity index regardless of planner choice. |
| `pk_catalog_course_offerings` | `(offering_id)` | Offering snapshot lookup by UUID. | Insert entry and uniqueness probe; key changes rewrite entries. Retained immutable keys reduce update churn. | On representative fixtures, compare actual/estimated rows, scan/filter work and buffers for the named equality/list query; keep required integrity index regardless of planner choice. |
| `uq_offering_term_course` | `(term_id, course_id)` | Term catalog/keyset order and one subject offering per term; term equality is the primary list prefix. | Insert entry and uniqueness probe; key changes rewrite entries. Retained immutable keys reduce update churn. | On representative fixtures, compare actual/estimated rows, scan/filter work and buffers for the named equality/list query; keep required integrity index regardless of planner choice. |
| `uq_offering_identity_term` | `(offering_id, term_id)` | Integrity target for section and offering-eligibility same-term references. | Insert entry and uniqueness probe; key changes rewrite entries. Retained immutable keys reduce update churn. | On representative fixtures, compare actual/estimated rows, scan/filter work and buffers for the named equality/list query; keep required integrity index regardless of planner choice. |
| `ix_offering_published_term` | `(term_id, course_id)` WHERE `publication_status = 'PUBLISHED'` | Term catalog pagination/filtering over published offerings; term equality first, course key next. Validate against the existing term/course UNIQUE before keeping this smaller partial read index. | Additional entries/WAL and vacuum work on insert/delete or indexed/predicate changes. | Use a query with the explicit constant predicate and realistic selectivity; compare actual rows, filtered rows and buffers against existing keys. Candidate only; remove if redundant. |
| `pk_sections_course_sections` | `(section_id)` | Section row lock by ID; do not lock a join of several entities. | Insert entry and uniqueness probe; key changes rewrite entries. Retained immutable keys reduce update churn. | On representative fixtures, compare actual/estimated rows, scan/filter work and buffers for the named equality/list query; keep required integrity index regardless of planner choice. |
| `uq_section_term_offering_code` | `(term_id, offering_id, section_code)` | List groups by known term/offering, then code; also prevent duplicate section codes within that offering. | Insert entry and uniqueness probe; key changes rewrite entries. Retained immutable keys reduce update churn. | On representative fixtures, compare actual/estimated rows, scan/filter work and buffers for the named equality/list query; keep required integrity index regardless of planner choice. |
| `uq_section_identity_offering_term` | `(section_id, offering_id, term_id)` | Integrity target for the enrollment section/offering/term tuple; not a second allocation key. | Insert entry and uniqueness probe; key changes rewrite entries. Retained immutable keys reduce update churn. | On representative fixtures, compare actual/estimated rows, scan/filter work and buffers for the named equality/list query; keep required integrity index regardless of planner choice. |
| `pk_sections_instructors` | `(instructor_id)` | Teacher lookup by stable synthetic ID. | Insert entry and uniqueness probe; key changes rewrite entries. Retained immutable keys reduce update churn. | On representative fixtures, compare actual/estimated rows, scan/filter work and buffers for the named equality/list query; keep required integrity index regardless of planner choice. |
| `pk_sections_section_instructors` | `(section_id, instructor_id)` | List teachers for a section and reject duplicate assignment; section prefix is the query direction. | Insert entry and uniqueness probe; key changes rewrite entries. Retained immutable keys reduce update churn. | On representative fixtures, compare actual/estimated rows, scan/filter work and buffers for the named equality/list query; keep required integrity index regardless of planner choice. |
| `pk_sections_section_meetings` | `(meeting_id)` | Occurrence lookup by stable ID for draft administration. | Insert entry and uniqueness probe; key changes rewrite entries. Retained immutable keys reduce update churn. | On representative fixtures, compare actual/estimated rows, scan/filter work and buffers for the named equality/list query; keep required integrity index regardless of planner choice. |
| `uq_meeting_section_interval` | `(section_id, starts_at, ends_at)` | Fetch all occurrences for section IDs in start order; reject exact duplicate intervals. | Insert entry and uniqueness probe; key changes rewrite entries. Retained immutable keys reduce update churn. | On representative fixtures, compare actual/estimated rows, scan/filter work and buffers for the named equality/list query; keep required integrity index regardless of planner choice. |
| `pk_catalog_offering_prerequisites` | `(offering_id, required_course_id)` | Fetch AND-list for an offering and reject duplicate required subjects; offering prefix first. | Insert entry and uniqueness probe; key changes rewrite entries. Retained immutable keys reduce update churn. | On representative fixtures, compare actual/estimated rows, scan/filter work and buffers for the named equality/list query; keep required integrity index regardless of planner choice. |
| `pk_students_eligibility_snapshots` | `(snapshot_id)` | Pinned evidence header lookup by snapshot UUID. | Insert entry and uniqueness probe; key changes rewrite entries. Retained immutable keys reduce update churn. | On representative fixtures, compare actual/estimated rows, scan/filter work and buffers for the named equality/list query; keep required integrity index regardless of planner choice. |
| `uq_snapshot_identity_student` | `(snapshot_id, student_id)` | Integrity target ensuring pinned evidence belongs to the student being coordinated. | Insert entry and uniqueness probe; key changes rewrite entries. Retained immutable keys reduce update churn. | On representative fixtures, compare actual/estimated rows, scan/filter work and buffers for the named equality/list query; keep required integrity index regardless of planner choice. |
| `uq_snapshot_student_source` | `(student_id, source_version)` | Find a student evidence version during setup and prevent duplicate provenance. | Insert entry and uniqueness probe; key changes rewrite entries. Retained immutable keys reduce update churn. | On representative fixtures, compare actual/estimated rows, scan/filter work and buffers for the named equality/list query; keep required integrity index regardless of planner choice. |
| `pk_students_completed_courses` | `(snapshot_id, course_id)` | Fetch all evidence or probe a subject within a snapshot; snapshot equality before course. | Insert entry and uniqueness probe; key changes rewrite entries. Retained immutable keys reduce update churn. | On representative fixtures, compare actual/estimated rows, scan/filter work and buffers for the named equality/list query; keep required integrity index regardless of planner choice. |
| `pk_rules_offering_policy_eligibility` | `(offering_id, policy_id)` | Probe an exact allowed offering/policy pair; offering list can share the prefix. | Insert entry and uniqueness probe; key changes rewrite entries. Retained immutable keys reduce update churn. | On representative fixtures, compare actual/estimated rows, scan/filter work and buffers for the named equality/list query; keep required integrity index regardless of planner choice. |
| `pk_enrollment_student_terms` | `(student_id, term_id)` | Exact student/term coordination lock; also supports student-first term lookup. | Insert entry and uniqueness probe; key changes rewrite entries. Retained immutable keys reduce update churn. | On representative fixtures, compare actual/estimated rows, scan/filter work and buffers for the named equality/list query; keep required integrity index regardless of planner choice. |
| `uq_student_term_policy` | `(student_id, term_id, policy_id)` | Integrity target pinning each enrollment to that student/term policy; not an independently mutable assignment. | Insert entry and uniqueness probe; key changes rewrite entries. Retained immutable keys reduce update churn. | On representative fixtures, compare actual/estimated rows, scan/filter work and buffers for the named equality/list query; keep required integrity index regardless of planner choice. |
| `pk_enrollment_enrollments` | `(enrollment_id)` | Stable episode lookup for withdrawal/audit, regardless of ACTIVE/WITHDRAWN status. | Insert entry and uniqueness probe; key changes rewrite entries. Retained immutable keys reduce update churn. | On representative fixtures, compare actual/estimated rows, scan/filter work and buffers for the named equality/list query; keep required integrity index regardless of planner choice. |
| `ux_enrollment_active_section_student` | `(section_id, student_id)` WHERE `status = 'ACTIVE'` | Mandatory unique active section membership; section-leading order also serves section counts/member lookup. | Entry/uniqueness probe on ACTIVE insert; withdrawal removes index membership. Status changes prevent treating this update as a free HOT path. | Use a query with the explicit constant predicate and realistic selectivity; compare actual rows, filtered rows and buffers against existing keys. Integrity indexes remain required even if a small-fixture scan is cheaper. |
| `ux_enrollment_active_student_offering` | `(student_id, offering_id)` WHERE `status = 'ACTIVE'` | Provisional mandatory unique active offering membership; student-leading order serves current membership/credit reads with term filtering. | Entry/uniqueness probe on ACTIVE insert; withdrawal removes index membership. Status changes prevent treating this update as a free HOT path. | Use a query with the explicit constant predicate and realistic selectivity; compare actual rows, filtered rows and buffers against existing keys. Integrity indexes remain required even if a small-fixture scan is cheaper. |
| `ix_enrollment_student_term_history` | `(student_id, term_id, enrolled_at DESC, enrollment_id)` | Own enrollment/history keyset pagination: equality student/term before time and tie-breaker. | Additional entries/WAL and vacuum work on insert/delete or indexed/predicate changes. | On representative fixtures, compare actual/estimated rows, scan/filter work and buffers for the named equality/list query; keep required integrity index regardless of planner choice. |
| `pk_operations_idempotency_records` | `(operation_id)` | Operation-instance lookup for finalization and cleanup; never reuse this UUID when replacing an expired scope. | Insert entry and uniqueness probe; key changes rewrite entries. Retained immutable keys reduce update churn. | On representative fixtures, compare actual/estimated rows, scan/filter work and buffers for the named equality/list query; keep required integrity index regardless of planner choice. |
| `uq_idempotency_scope` | `(principal_id, operation_type, operation_version, idempotency_key)` | Exact principal/operation/version/key lookup and unique claim conflict; all scope components use equality. | Insert entry and uniqueness probe; key changes rewrite entries. Retained immutable keys reduce update churn. | On representative fixtures, compare actual/estimated rows, scan/filter work and buffers for the named equality/list query; keep required integrity index regardless of planner choice. |
| `ix_idempotency_expiry` | `(expires_at, operation_id)` WHERE `state = 'COMPLETED'` | Bounded expiry scan ordered by expiration then stable operation identity; no clock-dependent index predicate. | Entry on completion, removal on expiry cleanup; claim itself is outside this partial index. | Use a query with the explicit constant predicate and realistic selectivity; compare actual rows, filtered rows and buffers against existing keys. Integrity indexes remain required even if a small-fixture scan is cheaper. |
| `pk_audit_audit_events` | `(audit_id)` | Stable event lookup. | Insert entry and uniqueness probe; key changes rewrite entries. Retained immutable keys reduce update churn. | On representative fixtures, compare actual/estimated rows, scan/filter work and buffers for the named equality/list query; keep required integrity index regardless of planner choice. |
| `uq_audit_operation_event_target` | `(operation_id, event_type, target_type, target_id)` | Reject a second identical state-change event for one operation/target; operation identity first. | Insert entry and uniqueness probe; key changes rewrite entries. Retained immutable keys reduce update churn. | On representative fixtures, compare actual/estimated rows, scan/filter work and buffers for the named equality/list query; keep required integrity index regardless of planner choice. |
| `ix_audit_enrollment_time` | `(enrollment_id, decided_at, audit_id)` WHERE `enrollment_id IS NOT NULL` | Bounded episode transition/history lookup with deterministic ordering; not a full JSON index. | Additional entries/WAL and vacuum work on insert/delete or indexed/predicate changes. | Use a query with the explicit constant predicate and realistic selectivity; compare actual rows, filtered rows and buffers against existing keys. Integrity indexes remain required even if a small-fixture scan is cheaper. |
| `pk_identity_spring_session` | `(primary_id)` | Framework session-row lookup and session-attribute FK anchor. | Insert entry and uniqueness probe; key changes rewrite entries. Retained immutable keys reduce update churn. | On representative fixtures, compare actual/estimated rows, scan/filter work and buffers for the named equality/list query; keep required integrity index regardless of planner choice. |
| `spring_session_ix1` | `(session_id)` | Framework cookie/session-ID resolution and uniqueness; internal primary_id remains the FK anchor. | Insert entry and uniqueness probe; key changes rewrite entries. Retained immutable keys reduce update churn. | On representative fixtures, compare actual/estimated rows, scan/filter work and buffers for the named equality/list query; keep required integrity index regardless of planner choice. |
| `spring_session_ix2` | `(expiry_time)` | Framework expiration cleanup; equality/range time scan. | Additional entries/WAL and vacuum work on insert/delete or indexed/predicate changes. | On representative fixtures, compare actual/estimated rows, scan/filter work and buffers for the named equality/list query; keep required integrity index regardless of planner choice. |
| `spring_session_ix3` | `(principal_name)` | Principal session lookup/revocation; account authority is checked separately. | Additional entries/WAL and vacuum work on insert/delete or indexed/predicate changes. | On representative fixtures, compare actual/estimated rows, scan/filter work and buffers for the named equality/list query; keep required integrity index regardless of planner choice. |
| `pk_identity_spring_session_attributes` | `(session_primary_id, attribute_name)` | Fetch/upsert/delete attributes within one session; session prefix then attribute key. | Insert entry and uniqueness probe; key changes rewrite entries. Retained immutable keys reduce update churn. | On representative fixtures, compare actual/estimated rows, scan/filter work and buffers for the named equality/list query; keep required integrity index regardless of planner choice. |

### Query shapes and indexes deliberately omitted

Active membership queries use `student_id`, `term_id`, and literal `status = 'ACTIVE'`; the student-leading offering unique index supplies the student prefix and the heap filters term. The history index supplies the student/term prefix for paginated all-state reads. A separate active student/term index is deferred unless many simultaneously active terms or query plans justify it. Section counts use the section-leading active unique index; no extra active-section index is necessary.

Section listing requires the API's known term and offering; the term/offering/code UNIQUE supports both. Meeting lookup batches section IDs through the section/time UNIQUE. Policy selection starts from the student's pinned policy, so a global timestamp/window index is unnecessary. No index is added solely for a foreign-key child when online parent deletion is prohibited and no required lookup needs it. Review parent-deletion plans separately before enabling a new lifecycle.

Title substring search can scan a small bounded catalog initially; no speculative trigram/full-text extension is introduced. Course-code prefix search is canonical and case-sensitive. A production-sized catalog may justify a separate measured search design. Partial active predicates must be visible to the planner; generic parameterized status queries may fail to use them. See [PostgreSQL Partial Indexes](https://www.postgresql.org/docs/current/indexes-partial.html).

Plan future `EXPLAIN (ANALYZE, BUFFERS)` comparisons with representative term/cohort/popularity distributions and warm/cold cache conditions. It executes the query: no EXPLAIN is run in this phase, and future write-plan experiments belong to an isolated disposable fixture, even if wrapped in rollback. Compare plans, row estimates, loops, buffers, latency, WAL and write throughput; EXPLAIN alone is not a contention benchmark. See [Using EXPLAIN](https://www.postgresql.org/docs/current/using-explain.html).

## Session schema compatibility

The two session tables follow the inspected [Spring Session PostgreSQL reference schema](https://github.com/spring-projects/spring-session/blob/main/spring-session-jdbc/src/main/resources/org/springframework/session/jdbc/schema-postgresql.sql). Choose the matching supported adapter version and qualified table-name configuration before implementation, not a custom hash-only session table presented as drop-in compatible. `principal_name` is intentionally nullable/unconstrained framework metadata; resolve canonical principal UUIDs against enabled accounts and auth_version on every protected request. An absent/deleted/disabled account fails authentication even if a session row exists. Credentials and transcript data never belong in session attributes.

## Review and Phase 4 handoff

The physical model now has executable Flyway migrations, guards, roles/helpers and small synthetic verification fixtures. The dictionary/real-catalog comparison and selected behavior checks passed; [Phase 4 verification](phase4-verification.md) distinguishes them from unexecuted full-use-case/load scenarios. [Data integrity](data-integrity.md) records DBF findings, unresolved academic choices, the synthetic dataset and future test matrix. Review remaining academic/operational decisions and AD-13 before separately authorizing Phase 5 Enrollment Implementation. Preserve all acceptance identifiers and do not report the 5,000-student workload hypothesis as achieved capacity.

## Phase 5 additive lock capability

F5-01 was reproduced under portal_runtime: PostgreSQL SELECT FOR UPDATE requires UPDATE privilege on a selected table. V7 adds enrollment.lock_student_term(uuid,uuid), a VOLATILE SECURITY DEFINER function owned by portal_owner with fixed pg_catalog search_path. Only portal_runtime may execute it; PUBLIC is revoked. The function performs the existing student_terms FOR UPDATE query in the caller's root transaction and returns policy_id, eligibility_snapshot_id and credit_limit_override. It cannot update/rekey academic facts and adds no table, column, FK or index. See [PostgreSQL SELECT permissions](https://www.postgresql.org/docs/18/sql-select.html) and [safe SECURITY DEFINER functions](https://www.postgresql.org/docs/18/sql-createfunction.html). The separate upgrade test preserves populated V6 data and all six original history checksums.
