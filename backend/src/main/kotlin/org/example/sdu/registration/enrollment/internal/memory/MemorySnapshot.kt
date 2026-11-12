package org.example.sdu.registration.enrollment.internal.memory

import org.example.sdu.registration.academicrules.api.*
import org.example.sdu.registration.catalog.api.*
import org.example.sdu.registration.enrollment.api.EnrollmentView
import org.example.sdu.registration.enrollment.internal.Assignment
import org.example.sdu.registration.sections.api.*
import org.example.sdu.registration.shared.database.*
import org.example.sdu.registration.shared.idempotency.*
import org.example.sdu.registration.students.api.*
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.stereotype.Component
import org.springframework.transaction.PlatformTransactionManager
import org.springframework.transaction.TransactionDefinition
import org.springframework.transaction.support.TransactionTemplate
import java.math.BigDecimal
import java.util.UUID

data class MemoryEpisode(val student: UUID, val policy: UUID, val view: EnrollmentView)
data class MemoryStudent(val assignment: Assignment, val evidence: AcademicEvidence?)
data class MemoryReplay(val operation: Operation, val outcome: StoredOutcome)
class MemoryState(
    val sections: MutableMap<UUID, Section>, val offerings: Map<UUID, Offering>, val meetings: List<Meeting>,
    val policies: Map<UUID, Policy>, val windows: Map<Pair<UUID, String>, Window>, val publishedTerms: Set<UUID>,
    val students: Map<Pair<UUID, UUID>, MemoryStudent>, val allowed: Set<Pair<UUID, UUID>>,
    val episodes: MutableMap<UUID, MemoryEpisode>, val outcomes: MutableMap<Triple<UUID, String, UUID>, MemoryReplay>
)
@Component
@ConditionalOnProperty(name = ["portal.write-behind.enabled"], havingValue = "true")
class MemorySnapshot(private val sql: Sql, private val facts: SectionFacts, private val catalog: CatalogFacts,
    private val policy: AcademicPolicy, private val evidence: StudentEvidence, manager: PlatformTransactionManager) {
    private val transaction = TransactionTemplate(manager).apply { isolationLevel = TransactionDefinition.ISOLATION_REPEATABLE_READ; timeout = 120; propagationBehavior = TransactionDefinition.PROPAGATION_REQUIRES_NEW }
    fun load(): MemoryState = requireNotNull(transaction.execute {
        val sections = sql.rows("SELECT section_id FROM sections.course_sections").associate { it.uuid("section_id").let { id -> id to requireNotNull(facts.locate(id)) } }.toMutableMap()
        val offerings = sql.rows("SELECT offering_id FROM catalog.course_offerings").associate { it.uuid("offering_id").let { id -> id to requireNotNull(catalog.offering(id)) } }
        val policies = sql.rows("SELECT policy_id FROM rules.registration_policies").associate { it.uuid("policy_id").let { id -> id to requireNotNull(policy.policy(id)) } }
        val windows = policies.keys.flatMap { id -> listOf("ENROLL", "WITHDRAW").mapNotNull { type -> policy.window(id, type)?.let { (id to type) to it } } }.toMap()
        val students = sql.rows("SELECT * FROM enrollment.student_terms").associate { row ->
            val assignment = Assignment(row.uuid("policy_id"), row.uuid("eligibility_snapshot_id"), row["credit_limit_override"] as BigDecimal?)
            (row.uuid("student_id") to row.uuid("term_id")) to MemoryStudent(assignment, evidence.pinned(row.uuid("student_id"), assignment.snapshot))
        }
        val episodes = sql.rows("SELECT * FROM enrollment.enrollments").associate { row ->
            row.uuid("enrollment_id") to MemoryEpisode(row.uuid("student_id"), row.uuid("policy_id"), EnrollmentView(
                row.uuid("enrollment_id"), row.uuid("section_id"), row.uuid("offering_id"), row.uuid("term_id"), row.text("status"),
                row.instant("enrolled_at"), if (row["withdrawn_at"] == null) null else row.instant("withdrawn_at")))
        }.toMutableMap()
        val outcomes = sql.rows("SELECT r.*,coalesce((SELECT a.details->>'responseJson' FROM audit.audit_events a WHERE a.operation_id=r.operation_id LIMIT 1),r.response_body::text) AS body FROM operations.idempotency_records r WHERE state='COMPLETED' AND operation_type IN ('ENROLL','WITHDRAW') AND expires_at>clock_timestamp()").associate { row ->
            val op = Operation(row.uuid("principal_id"), row.text("operation_type"), row.uuid("target_id"), row.uuid("idempotency_key"))
            Triple(op.principal, op.type, op.key) to MemoryReplay(op, StoredOutcome((row["response_status"] as Number).toInt(), row.text("response_code"), row.text("body")))
        }.toMutableMap()
        sections.values.forEach { section ->
            check(section.count in 0..section.capacity && section.count == episodes.values.count { it.view.sectionId == section.id && it.view.status == "ACTIVE" })
        }
        MemoryState(sections, offerings, facts.meetings(sections.keys), policies, windows,
            sql.rows("SELECT term_id FROM rules.term_policy_gates WHERE configuration_state='PUBLISHED'").map { it.uuid("term_id") }.toSet(),
            students, sql.rows("SELECT offering_id,policy_id FROM rules.offering_policy_eligibility").map { it.uuid("offering_id") to it.uuid("policy_id") }.toSet(), episodes, outcomes)
    })
}
