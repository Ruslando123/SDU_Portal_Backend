package org.example.sdu.verification

import org.junit.jupiter.api.Assertions.assertEquals

object Phase5InvariantChecks {
    fun verify(db: TestDatabase) {
        db.invariants()
        assertEquals(0L,db.count("""
            SELECT count(*) FROM enrollment.enrollments a
            JOIN enrollment.enrollments b ON a.student_id=b.student_id AND a.term_id=b.term_id AND a.enrollment_id<b.enrollment_id
            JOIN sections.section_meetings x ON x.section_id=a.section_id AND x.is_required
            JOIN sections.section_meetings y ON y.section_id=b.section_id AND y.is_required
            WHERE a.status='ACTIVE' AND b.status='ACTIVE' AND x.starts_at<y.ends_at AND y.starts_at<x.ends_at
        """),"No required occurrence clashes")
        assertEquals(0L,db.count("""
            SELECT count(*) FROM enrollment.enrollments e
            LEFT JOIN audit.audit_events a ON a.enrollment_id=e.enrollment_id AND a.event_type='ENROLLED'
            WHERE a.audit_id IS NULL
        """),"Every retained episode has its transactional enrollment audit")
        assertEquals(0L,db.count("""
            SELECT count(*) FROM enrollment.enrollments e
            LEFT JOIN audit.audit_events a ON a.enrollment_id=e.enrollment_id AND a.event_type='WITHDRAWN'
            WHERE e.status='WITHDRAWN' AND a.audit_id IS NULL
        """),"Every withdrawal has its transactional state change audit")
        assertEquals(0L,db.count("""
            SELECT count(*) FROM operations.idempotency_records
            WHERE state<>'COMPLETED' OR response_body->>'code'<>response_code
        """),"No incomplete or inconsistent terminal outcomes")
    }
}
