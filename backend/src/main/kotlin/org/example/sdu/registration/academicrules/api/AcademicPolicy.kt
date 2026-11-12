package org.example.sdu.registration.academicrules.api

import java.math.BigDecimal
import java.time.Instant
import java.util.UUID

data class Policy(val limit: BigDecimal)
data class Window(val id: UUID, val opensAt: Instant, val closesAt: Instant, val closed: Boolean) {
    fun permits(at: Instant): Boolean = !closed && !at.isBefore(opensAt) && at.isBefore(closesAt)
}
interface AcademicPolicy {
    fun protect(term: UUID): Boolean?
    fun policy(id: UUID): Policy?
    fun window(policy: UUID, operation: String): Window?
    fun allowed(offering: UUID, policy: UUID): Boolean
}
/** Immutable scalar facts supplied by Enrollment; this pure evaluator has no persistence dependencies. */
data class AcademicChecks(val duplicate: Boolean, val evidenceComplete: Boolean, val active: Boolean, val allowed: Boolean,
    val prerequisitesMet: Boolean, val creditTotal: BigDecimal, val limit: BigDecimal, val meetingConflict: Boolean)
object EvaluateEnrollmentRules {
    fun denial(facts: AcademicChecks): String? = when {
        facts.duplicate -> "ALREADY_ENROLLED"
        !facts.evidenceComplete -> "ELIGIBILITY_DATA_UNAVAILABLE"
        !facts.active -> "ACADEMIC_INELIGIBLE"
        !facts.allowed -> "COHORT_INELIGIBLE"
        !facts.prerequisitesMet -> "PREREQUISITE_NOT_MET"
        facts.creditTotal > facts.limit -> "CREDIT_LIMIT_EXCEEDED"
        facts.meetingConflict -> "SCHEDULE_CONFLICT"
        else -> null
    }
}
