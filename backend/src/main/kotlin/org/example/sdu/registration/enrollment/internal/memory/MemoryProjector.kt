package org.example.sdu.registration.enrollment.internal.memory

import org.example.sdu.registration.academicrules.api.AcademicPolicy
import org.example.sdu.registration.enrollment.internal.EnrollmentRepository
import org.example.sdu.registration.enrollment.internal.EnrollmentSettings
import org.example.sdu.registration.sections.api.SectionAllocation
import org.example.sdu.registration.shared.database.*
import org.example.sdu.registration.shared.idempotency.DurableOutcomes
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.stereotype.Component
import org.springframework.transaction.PlatformTransactionManager
import org.springframework.transaction.support.TransactionTemplate
import tools.jackson.databind.json.JsonMapper
import java.security.MessageDigest
import java.util.UUID

@Component
@ConditionalOnProperty(name = ["portal.write-behind.enabled"], havingValue = "true")
class MemoryProjector(private val sql: Sql, private val outcomes: DurableOutcomes, private val repository: EnrollmentRepository,
    private val policies: AcademicPolicy, private val allocation: SectionAllocation, private val mapper: JsonMapper,
    settings: EnrollmentSettings, private val memory: MemorySettings, manager: PlatformTransactionManager) {
    private val retention = settings.retentionSeconds
    private val transaction = TransactionTemplate(manager).apply { timeout = 10; propagationBehavior = org.springframework.transaction.TransactionDefinition.PROPAGATION_REQUIRES_NEW }
    fun project(bytes: ByteArray) {
        val event = MemoryEvent.decode(bytes, mapper)
        val digest = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
        transaction.executeWithoutResult {
            sql.one("SELECT set_config('lock_timeout','2s',true),set_config('statement_timeout','5s',true)")
            if (memory.projectorMode == "sql") {
                requireNotNull(sql.one("SELECT enrollment.project_memory_decision(CAST(? AS jsonb),?,?,?,?) AS inserted",
                    bytes.toString(Charsets.UTF_8), digest, event.operation.fingerprint(), event.outcome(mapper).body, retention))
                return@executeWithoutResult
            }
            // Immutable audit identity is a permanent inbox key, including after idempotency retention expires.
            val existing = sql.one("SELECT details->>'journalHash' AS hash FROM audit.audit_events WHERE audit_id=?", event.id)
            if (existing != null) {
                check(existing["hash"] == digest) { "Journal identity collision" }
                return@executeWithoutResult
            }
            val claim = outcomes.claim(event.operation)
            check(claim.replay == null) { "Unexpected independent writer or journal ordering conflict" }
            val operationId = requireNotNull(claim.operationId)
            if (event.code in setOf("ENROLLED", "WITHDRAWN")) {
                check(policies.protect(requireNotNull(event.term)) == true)
                val assignment = requireNotNull(repository.lockStudent(event.student, event.term))
                check(assignment.policy == event.policy)
                val section = requireNotNull(allocation.lock(requireNotNull(event.section)))
                check(section.term == event.term && section.offering == event.offering)
                if (event.code == "ENROLLED") {
                    check(repository.active(event.student, event.term).none { it.view.offeringId == event.offering })
                    check(allocation.allocate(section.id)) { "Projection capacity conflict" }
                    repository.insert(requireNotNull(event.enrollment), event.student, event.term, assignment.policy,
                        section.offering, section.id, requireNotNull(event.window), event.time)
                } else {
                    val episode = requireNotNull(repository.own(event.student, requireNotNull(event.enrollment)))
                    check(episode.view.status == "ACTIVE" && episode.view.sectionId == section.id)
                    repository.withdraw(event.student, event.enrollment, requireNotNull(event.window), event.time)
                    allocation.release(section.id)
                }
            }
            check(sql.update("""
                INSERT INTO audit.audit_events (audit_id,operation_id,actor_principal_id,event_type,target_type,target_id,enrollment_id,decided_at,correlation_id,details)
                VALUES (?,?,?,?,?,?,?,?,?,CAST(? AS jsonb))
            """, event.id, operationId, event.operation.principal, event.code, event.operation.targetType, event.operation.target,
                event.enrollment, event.time, UUID.fromString(event.body["correlationId"] as String),
                mapper.writeValueAsString(mapOf("code" to event.code, "journalHash" to digest, "decisionAuthority" to "rabbitmq-memory-v1", "responseJson" to event.outcome(mapper).body))) == 1)
            outcomes.complete(operationId, event.status, event.code, event.body, event.time, retention)
        }
        // Returning from TransactionTemplate means COMMIT finished; only then may the journal ACK.
    }
}
