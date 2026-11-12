package org.example.sdu.registration.enrollment.internal

import org.example.sdu.registration.academicrules.api.*
import org.example.sdu.registration.authentication.api.Actor
import org.example.sdu.registration.catalog.api.CatalogFacts
import org.example.sdu.registration.enrollment.api.CommittedOutcome
import org.example.sdu.registration.sections.api.Section
import org.example.sdu.registration.sections.api.SectionAllocation
import org.example.sdu.registration.sections.api.SectionFacts
import org.example.sdu.registration.shared.database.*
import org.example.sdu.registration.shared.errors.ApiFailure
import org.example.sdu.registration.shared.idempotency.DurableOutcomes
import org.example.sdu.registration.shared.idempotency.Operation
import org.example.sdu.registration.shared.observability.EnrollmentMetrics
import org.example.sdu.registration.students.api.StudentEvidence
import org.slf4j.MDC
import org.springframework.stereotype.Service
import org.springframework.transaction.annotation.Isolation
import org.springframework.transaction.annotation.Transactional
import org.springframework.transaction.support.TransactionSynchronizationManager
import java.math.BigDecimal
import java.time.Instant
import java.util.UUID

/** Called only by the nontransactional orchestrator through Spring's transaction proxy. */
@Service
class EnrollmentAttempt(
    private val outcomes: DurableOutcomes, private val repository: EnrollmentRepository,
    private val policies: AcademicPolicy, private val evidence: StudentEvidence, private val catalog: CatalogFacts,
    private val facts: SectionFacts, private val allocation: SectionAllocation, private val sql: Sql,
    private val settings: EnrollmentSettings, private val metrics: EnrollmentMetrics
) {
    @Transactional(isolation = Isolation.READ_COMMITTED, timeoutString = "\${enrollment.transaction-timeout-seconds:10}")
    fun execute(actor: Actor, operation: Operation, deadline: Long, proxyStarted: Long): CommittedOutcome {
        val entered = System.nanoTime()
        val type = operation.type.lowercase()
        metrics.duration("connection.begin.$type", proxyStarted)
        metrics.untilCompletion("connection.transaction.$type", entered)
        check(TransactionSynchronizationManager.isActualTransactionActive())
        check(actor.role == "STUDENT" && actor.studentId != null && operation.principal == actor.principalId)
        val budget = ((deadline - System.nanoTime()) / 1_000_000).coerceAtMost(settings.statementTimeoutMs).coerceAtLeast(1)
        sql.one("SELECT set_config('lock_timeout',?,true),set_config('statement_timeout',?,true)", "${minOf(settings.lockTimeoutMs, budget)}ms", "${budget}ms")
        metrics.observeCommit()
        val claim = metrics.timed("idempotency.claim") { metrics.stage("idempotency") { outcomes.claim(operation) } }
        claim.replay?.let { return it }
        val id = requireNotNull(claim.operationId)
        return if (operation.type == "ENROLL") enroll(actor, operation, id, deadline) else withdraw(actor, operation, id, deadline)
    }

    private fun lockSection(id: UUID): Section {
        val section = metrics.timed("section.lock.wait") { allocation.lock(id) }
            ?: throw ApiFailure(503, "TEMPORARILY_UNAVAILABLE", true)
        metrics.untilCompletion("section.lock.hold")
        return section
    }

    private fun protect(term: UUID): Boolean = metrics.stage("gate") { policies.protect(term) } ?: throw ApiFailure(503, "TEMPORARILY_UNAVAILABLE", true)

    private fun enroll(actor: Actor, operation: Operation, operationId: UUID, deadline: Long): CommittedOutcome {
        val student = requireNotNull(actor.studentId)
        val hint = facts.locate(operation.target) ?: return finish(actor, operation, operationId, 404, "RESOURCE_NOT_FOUND", sql.clock(), deadline)
        if (!protect(hint.term)) return finish(actor, operation, operationId, 409, "REGISTRATION_CLOSED", sql.clock(), deadline)
        val assignment = metrics.stage("student") { repository.lockStudent(student, hint.term) } ?: throw ApiFailure(503, "ELIGIBILITY_DATA_UNAVAILABLE", true)
        val earlySection = if (settings.benchmarkEarlySectionLocking) lockSection(hint.id) else null
        val academicStart = System.nanoTime()
        val policy = policies.policy(assignment.policy) ?: throw ApiFailure(503, "TEMPORARILY_UNAVAILABLE", true)
        val offering = catalog.offering(hint.offering) ?: throw ApiFailure(503, "TEMPORARILY_UNAVAILABLE", true)
        val studentEvidence = evidence.pinned(student, assignment.snapshot)
        val active = repository.active(student, hint.term)
        val activeCredits = catalog.credits(active.map { it.view.offeringId }.toSet())
        check(activeCredits.size == active.map { it.view.offeringId }.toSet().size)
        val meetings = facts.meetings(active.map { it.view.sectionId }.toSet() + hint.id).filter { it.required }
        val candidateMeetings = meetings.filter { it.section == hint.id }
        val existingMeetings = meetings.filter { it.section != hint.id }
        val prerequisitesMet = offering.prerequisites.all { prerequisite ->
            studentEvidence?.completions?.any { completion ->
                completion.courseId == prerequisite.courseId && completion.passed &&
                    (prerequisite.minimumGrade == null || (completion.scheme == prerequisite.scheme &&
                        completion.grade != null && completion.grade >= prerequisite.minimumGrade))
            } == true
        }
        val checks = AcademicChecks(
            active.any { it.view.sectionId == hint.id || it.view.offeringId == offering.id },
            studentEvidence?.complete == true, studentEvidence?.active == true, policies.allowed(offering.id, assignment.policy),
            prerequisitesMet, activeCredits.values.fold(BigDecimal.ZERO, BigDecimal::add) + offering.credits,
            assignment.override ?: policy.limit,
            candidateMeetings.any { candidate -> existingMeetings.any { current -> candidate.startsAt < current.endsAt && current.startsAt < candidate.endsAt } }
        )
        // AD-13: expensive immutable checks above; gate/student are still held while waiting here.
        metrics.duration("academic.checks", academicStart)
        // The term gate protects administrative window changes. Natural closing is checked at the post-lock time.
        val window = metrics.timed("decision.window") { policies.window(assignment.policy, "ENROLL") }
        val section = earlySection ?: lockSection(hint.id)
        check(section.offering == hint.offering && section.term == hint.term && offering.term == hint.term)
        val time = metrics.timed("decision.clock") { sql.clock() }
        if (!section.published || !offering.published) return finish(actor, operation, operationId, 404, "RESOURCE_NOT_FOUND", time, deadline)
        if (window == null || !window.permits(time)) return finish(actor, operation, operationId, 409, "REGISTRATION_CLOSED", time, deadline)
        val denial = EvaluateEnrollmentRules.denial(checks)
        if (denial == "ELIGIBILITY_DATA_UNAVAILABLE") throw ApiFailure(503, denial, true)
        if (denial != null) return finish(actor, operation, operationId, if (denial == "COHORT_INELIGIBLE") 403 else 409, denial, time, deadline)
        if (!metrics.timed("seat.write") { allocation.allocate(section.id) }) return finish(actor, operation, operationId, 409, "SECTION_FULL", time, deadline)
        val enrollmentId = UUID.randomUUID()
        metrics.timed("episode.write") { repository.insert(enrollmentId, student, hint.term, assignment.policy, hint.offering, hint.id, window.id, time) }
        return finish(actor, operation, operationId, 201, "ENROLLED", time, deadline, enrollmentId, hint.id, hint.term, "ACTIVE")
    }

    private fun withdraw(actor: Actor, operation: Operation, operationId: UUID, deadline: Long): CommittedOutcome {
        val student = requireNotNull(actor.studentId)
        val hint = repository.own(student, operation.target) ?: return finish(actor, operation, operationId, 404, "RESOURCE_NOT_FOUND", sql.clock(), deadline)
        if (!protect(hint.view.termId)) return finish(actor, operation, operationId, 409, "REGISTRATION_CLOSED", sql.clock(), deadline)
        val assignment = metrics.stage("student") { repository.lockStudent(student, hint.view.termId) } ?: throw ApiFailure(503, "TEMPORARILY_UNAVAILABLE", true)
        // Other enrollment mutations for this student and term cannot pass the held student-term lock.
        val episode = metrics.timed("withdraw.recheck") { repository.own(student, operation.target) } ?: error("Protected episode disappeared")
        check(episode.policy == assignment.policy && episode.view.enrollmentId == hint.view.enrollmentId && episode.view.sectionId == hint.view.sectionId && episode.view.termId == hint.view.termId)
        val window = if (episode.view.status == "WITHDRAWN") null
            else metrics.timed("decision.window") { policies.window(assignment.policy, "WITHDRAW") }
        val section = lockSection(episode.view.sectionId)
        check(section.term == episode.view.termId && section.offering == episode.view.offeringId)
        val time = metrics.timed("decision.clock") { sql.clock() }
        if (episode.view.status == "WITHDRAWN") return finish(actor, operation, operationId, 200, "ALREADY_WITHDRAWN", time, deadline,
            episode.view.enrollmentId, section.id, section.term, "WITHDRAWN", episode.view.withdrawnAt)
        if (window == null || !window.permits(time)) return finish(actor, operation, operationId, 409, "REGISTRATION_CLOSED", time, deadline)
        metrics.timed("episode.write") { repository.withdraw(student, operation.target, window.id, time) }
        metrics.timed("seat.write") { allocation.release(section.id) }
        return finish(actor, operation, operationId, 200, "WITHDRAWN", time, deadline, operation.target, section.id, section.term, "WITHDRAWN", time)
    }

    private fun finish(actor: Actor, operation: Operation, id: UUID, status: Int, code: String, time: Instant, deadline: Long,
        enrollment: UUID? = null, section: UUID? = null, term: UUID? = null, state: String? = null, withdrawnAt: Instant? = null): CommittedOutcome {
        if (System.nanoTime() >= deadline) throw ApiFailure(503, "TEMPORARILY_UNAVAILABLE", true)
        val correlation = MDC.get("correlationId")?.let(UUID::fromString) ?: UUID.randomUUID()
        val body = linkedMapOf<String, Any?>("code" to code, "decidedAt" to time.toString(), "correlationId" to correlation.toString(), "retryable" to false)
        if (status >= 400) body["message"] = when (code) {
            "SECTION_FULL" -> "The section has no available seats."
            "ALREADY_ENROLLED" -> "An active enrollment already exists for this offering."
            "CREDIT_LIMIT_EXCEEDED" -> "The request exceeds the term credit limit."
            "SCHEDULE_CONFLICT" -> "A required meeting conflicts with an active enrollment."
            "PREREQUISITE_NOT_MET" -> "A prerequisite has not been met."
            "REGISTRATION_CLOSED" -> "Registration is closed for this operation."
            "COHORT_INELIGIBLE" -> "The offering is not available under the assigned policy."
            "ACADEMIC_INELIGIBLE" -> "The academic status does not permit enrollment."
            "RESOURCE_NOT_FOUND" -> "The resource was not found."
            else -> "The request was denied."
        }
        if (enrollment != null) body.putAll(mapOf("enrollmentId" to enrollment.toString(), "sectionId" to section.toString(), "termId" to term.toString(), "status" to state, "withdrawnAt" to withdrawnAt?.toString()))
        metrics.timed("audit.write") { check(sql.update("""
            INSERT INTO audit.audit_events (audit_id,operation_id,actor_principal_id,event_type,target_type,target_id,enrollment_id,decided_at,correlation_id,details)
            VALUES (?,?,?,?,?,?,?,?,?,CAST(? AS jsonb))
        """, UUID.randomUUID(), id, actor.principalId, code, operation.targetType, operation.target, enrollment, time, correlation, "{\"code\":\"$code\"}") == 1) }
        return metrics.timed("idempotency.complete") { outcomes.complete(id, status, code, body, time, settings.retentionSeconds) }
    }
}
