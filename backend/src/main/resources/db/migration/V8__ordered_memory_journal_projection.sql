SET ROLE portal_owner;

-- Experimental RAM/RabbitMQ projector only. One event, one caller-owned transaction.
-- SECURITY INVOKER adds no privileges; the synchronous Enrollment path does not use this function.
-- Order is unchanged: inbox check, idempotency claim, term gate, student-term, section,
-- conditional seat/episode write, audit, completed outcome, then caller COMMIT and broker ACK.
CREATE FUNCTION enrollment.project_memory_decision(
    p_event jsonb, p_hash text, p_fingerprint bytea, p_response_json text, p_retention_seconds bigint)
RETURNS boolean LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = pg_catalog AS $project$
DECLARE
    event_id uuid := (p_event->>'id')::uuid;
    principal uuid := (p_event->>'principal')::uuid;
    student uuid := (p_event->>'student')::uuid;
    kind text := p_event->>'type';
    target uuid := (p_event->>'target')::uuid;
    idem_key uuid := (p_event->>'key')::uuid;
    decided timestamptz := (p_event->>'time')::timestamptz;
    code text := p_event->>'code';
    target_kind text := CASE WHEN kind='ENROLL' THEN 'SECTION' ELSE 'ENROLLMENT' END;
    episode_id uuid := (p_event->>'enrollment')::uuid;
    term uuid := (p_event->>'term')::uuid;
    event_section uuid := (p_event->>'section')::uuid;
    offering uuid := (p_event->>'offering')::uuid;
    policy uuid := (p_event->>'policy')::uuid;
    window_id uuid := (p_event->>'window')::uuid;
    stored_hash text;
    claim_id uuid;
    previous operations.idempotency_records%ROWTYPE;
    assignment record;
    section sections.course_sections%ROWTYPE;
    configuration text;
    affected integer;
BEGIN
    IF (p_event->>'version')::integer IS DISTINCT FROM 1 OR kind NOT IN ('ENROLL','WITHDRAW')
       OR p_hash !~ '^[0-9a-f]{64}$' OR octet_length(p_fingerprint)<>32
       OR p_retention_seconds IS NULL OR p_retention_seconds<=0
       OR p_response_json::jsonb IS DISTINCT FROM p_event->'body' THEN
        RAISE EXCEPTION 'Invalid memory journal event' USING ERRCODE='22023';
    END IF;
    SELECT a.details->>'journalHash' INTO stored_hash FROM audit.audit_events a WHERE a.audit_id=event_id;
    IF FOUND THEN
        IF stored_hash IS DISTINCT FROM p_hash THEN
            RAISE EXCEPTION 'Journal identity collision' USING ERRCODE='23514';
        END IF;
        RETURN false;
    END IF;
    FOR attempt IN 1..8 LOOP
        claim_id := gen_random_uuid();
        INSERT INTO operations.idempotency_records
            (operation_id,principal_id,operation_type,operation_version,idempotency_key,request_fingerprint,target_type,target_id,state,created_at)
        VALUES (claim_id,principal,kind,1,idem_key,p_fingerprint,target_kind,target,'CLAIMED',clock_timestamp())
        ON CONFLICT (principal_id,operation_type,operation_version,idempotency_key) DO NOTHING;
        GET DIAGNOSTICS affected = ROW_COUNT;
        EXIT WHEN affected=1;
        SELECT r.* INTO previous FROM operations.idempotency_records r
        WHERE r.principal_id=principal AND r.operation_type=kind AND r.operation_version=1 AND r.idempotency_key=idem_key FOR UPDATE;
        IF FOUND THEN
            IF previous.state<>'COMPLETED' OR previous.expires_at>clock_timestamp() THEN
                RAISE EXCEPTION 'Independent writer or journal ordering conflict' USING ERRCODE='23514';
            END IF;
            IF NOT operations.delete_expired_outcome(previous.operation_id) THEN
                RAISE EXCEPTION 'Expired outcome cleanup conflict' USING ERRCODE='23514';
            END IF;
        END IF;
    END LOOP;
    IF affected<>1 THEN RAISE EXCEPTION 'Idempotency claim exhausted' USING ERRCODE='23514'; END IF;
    IF code IN ('ENROLLED','WITHDRAWN') THEN
        SELECT g.configuration_state INTO configuration FROM rules.term_policy_gates g WHERE g.term_id=term FOR SHARE;
        IF configuration IS DISTINCT FROM 'PUBLISHED' THEN RAISE EXCEPTION 'Term projection conflict' USING ERRCODE='23514'; END IF;
        SELECT * INTO assignment FROM enrollment.lock_student_term(student,term);
        IF NOT FOUND OR assignment.policy_id IS DISTINCT FROM policy THEN RAISE EXCEPTION 'Assignment projection conflict' USING ERRCODE='23514'; END IF;
        SELECT s.* INTO section FROM sections.course_sections s WHERE s.section_id=event_section FOR UPDATE;
        IF NOT FOUND OR section.term_id IS DISTINCT FROM term OR section.offering_id IS DISTINCT FROM offering THEN
            RAISE EXCEPTION 'Section projection conflict' USING ERRCODE='23514';
        END IF;
        IF code='ENROLLED' THEN
            IF EXISTS(SELECT 1 FROM enrollment.enrollments e WHERE e.student_id=student AND e.term_id=term AND e.status='ACTIVE' AND e.offering_id=offering) THEN
                RAISE EXCEPTION 'Active enrollment projection conflict' USING ERRCODE='23514';
            END IF;
            UPDATE sections.course_sections s SET enrolled_count=s.enrolled_count+1
            WHERE s.section_id=event_section AND s.enrolled_count<s.capacity;
            GET DIAGNOSTICS affected=ROW_COUNT;
            IF affected<>1 THEN RAISE EXCEPTION 'Projection capacity conflict' USING ERRCODE='23514'; END IF;
            INSERT INTO enrollment.enrollments
                (enrollment_id,student_id,term_id,policy_id,offering_id,section_id,enroll_window_id,status,enrolled_at)
            VALUES(episode_id,student,term,policy,offering,event_section,window_id,'ACTIVE',decided);
        ELSE
            UPDATE enrollment.enrollments e SET status='WITHDRAWN',withdrawn_at=decided,withdraw_window_id=window_id
            WHERE e.enrollment_id=episode_id AND e.student_id=student AND e.section_id=event_section AND e.status='ACTIVE';
            GET DIAGNOSTICS affected=ROW_COUNT;
            IF affected<>1 THEN RAISE EXCEPTION 'Withdrawal ownership/state conflict' USING ERRCODE='23514'; END IF;
            UPDATE sections.course_sections s SET enrolled_count=s.enrolled_count-1 WHERE s.section_id=event_section AND s.enrolled_count>0;
            GET DIAGNOSTICS affected=ROW_COUNT;
            IF affected<>1 THEN RAISE EXCEPTION 'Invalid release count' USING ERRCODE='23514'; END IF;
        END IF;
    END IF;
    INSERT INTO audit.audit_events
        (audit_id,operation_id,actor_principal_id,event_type,target_type,target_id,enrollment_id,decided_at,correlation_id,details)
    VALUES(event_id,claim_id,principal,code,target_kind,target,episode_id,decided,(p_event->'body'->>'correlationId')::uuid,
        jsonb_build_object('code',code,'journalHash',p_hash,'decisionAuthority','rabbitmq-memory-v1','responseJson',p_response_json));
    UPDATE operations.idempotency_records r SET state='COMPLETED',decided_at=decided,
        response_status=(p_event->>'status')::smallint,response_code=code,response_body=p_event->'body',
        expires_at=decided+make_interval(secs=>p_retention_seconds::double precision)
    WHERE r.operation_id=claim_id AND r.state='CLAIMED';
    GET DIAGNOSTICS affected=ROW_COUNT;
    IF affected<>1 THEN RAISE EXCEPTION 'Outcome completion conflict' USING ERRCODE='23514'; END IF;
    RETURN true;
END
$project$;
REVOKE ALL ON FUNCTION enrollment.project_memory_decision(jsonb,text,bytea,text,bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION enrollment.project_memory_decision(jsonb,text,bytea,text,bigint) TO portal_runtime;
RESET ROLE;
