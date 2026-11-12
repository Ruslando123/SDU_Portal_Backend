package org.example.sdu.registration.students.internal

import org.example.sdu.registration.shared.database.*
import org.example.sdu.registration.students.api.*
import org.springframework.beans.factory.annotation.Value
import org.springframework.stereotype.Service
import org.springframework.transaction.annotation.Propagation
import org.springframework.transaction.annotation.Transactional
import java.math.BigDecimal
import java.util.UUID

@Service
@Transactional(propagation = Propagation.MANDATORY)
class JdbcStudentEvidence(private val sql: Sql,
    @Value("\${portal.performance.joined-academic-reads:false}") private val joinedAcademicReads: Boolean,
) : StudentEvidence {
    override fun pinned(student: UUID, snapshot: UUID): AcademicEvidence? {
        if (joinedAcademicReads) {
            val rows = sql.rows("""
                SELECT s.evidence_state,s.academic_status,c.course_id,c.passed,c.grade_value,c.grade_scheme
                FROM students.eligibility_snapshots s
                LEFT JOIN students.completed_courses c ON c.snapshot_id=s.snapshot_id
                WHERE s.snapshot_id=? AND s.student_id=?
            """, snapshot, student)
            val row = rows.firstOrNull() ?: return null
            return evidence(row, rows.filter { it["course_id"] != null }.map(::completion))
        }
        val row = sql.one("SELECT evidence_state,academic_status FROM students.eligibility_snapshots WHERE snapshot_id=? AND student_id=?", snapshot, student) ?: return null
        val completions = sql.rows("SELECT course_id,passed,grade_value,grade_scheme FROM students.completed_courses WHERE snapshot_id=?", snapshot)
            .map(::completion)
        return evidence(row, completions)
    }

    private fun completion(row: Row) = Completion(row.uuid("course_id"), row["passed"] as Boolean,
        row["grade_value"] as BigDecimal?, row["grade_scheme"] as String?)

    private fun evidence(row: Row, completions: List<Completion>) =
        AcademicEvidence(row["evidence_state"] == "COMPLETE", row["academic_status"] == "ACTIVE", completions)
}
