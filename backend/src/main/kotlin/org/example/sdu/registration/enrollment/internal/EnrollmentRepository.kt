package org.example.sdu.registration.enrollment.internal

import org.example.sdu.registration.enrollment.api.EnrollmentView
import org.example.sdu.registration.shared.database.*
import org.springframework.stereotype.Repository
import org.springframework.transaction.annotation.Propagation
import org.springframework.transaction.annotation.Transactional
import java.math.BigDecimal
import java.time.Instant
import java.util.UUID

data class Assignment(val policy: UUID, val snapshot: UUID, val override: BigDecimal?)
data class Episode(val view: EnrollmentView, val policy: UUID)
@Repository
@Transactional(propagation = Propagation.MANDATORY)
class EnrollmentRepository(private val sql: Sql, private val metrics: org.example.sdu.registration.shared.observability.EnrollmentMetrics) {
    fun lockStudent(student: UUID, term: UUID): Assignment? = metrics.stage("student_lock_query") { val start = System.nanoTime(); try { sql.one("SELECT * FROM enrollment.lock_student_term(?,?)", student, term) } finally { metrics.duration("student.lock.wait", start) } }
        ?.let { Assignment(it.uuid("policy_id"), it.uuid("eligibility_snapshot_id"), it["credit_limit_override"] as BigDecimal?) }
    fun active(student: UUID, term: UUID): List<Episode> = sql.rows("SELECT * FROM enrollment.enrollments WHERE student_id=? AND term_id=? AND status='ACTIVE'", student, term).map(::episode)
    fun own(student: UUID, id: UUID): Episode? = sql.one("SELECT * FROM enrollment.enrollments WHERE student_id=? AND enrollment_id=?", student, id)?.let(::episode)
    fun list(student: UUID, term: UUID, limit: Int, offset: Int): List<EnrollmentView> = sql.rows("SELECT * FROM enrollment.enrollments WHERE student_id=? AND term_id=? ORDER BY enrolled_at DESC,enrollment_id LIMIT ? OFFSET ?", student, term, limit, offset).map { episode(it).view }
    fun insert(id: UUID, student: UUID, term: UUID, policy: UUID, offering: UUID, section: UUID, window: UUID, time: Instant) {
        check(sql.update("INSERT INTO enrollment.enrollments VALUES (?,?,?,?,?,?,?,'ACTIVE',?,NULL,NULL)", id, student, term, policy, offering, section, window, time) == 1)
    }
    fun withdraw(student: UUID, id: UUID, window: UUID, time: Instant) {
        check(sql.update("UPDATE enrollment.enrollments SET status='WITHDRAWN',withdrawn_at=?,withdraw_window_id=? WHERE enrollment_id=? AND student_id=? AND status='ACTIVE'", time, window, id, student) == 1)
    }
    private fun episode(row: Row) = Episode(EnrollmentView(row.uuid("enrollment_id"), row.uuid("section_id"), row.uuid("offering_id"), row.uuid("term_id"), row.text("status"), row.instant("enrolled_at"), if(row["withdrawn_at"] == null) null else row.instant("withdrawn_at")), row.uuid("policy_id"))
}
