package org.example.sdu.registration.enrollment.api

import java.time.Instant
import java.util.UUID

typealias CommittedOutcome = org.example.sdu.registration.shared.idempotency.StoredOutcome
data class EnrollmentView(val enrollmentId: UUID, val sectionId: UUID, val offeringId: UUID, val termId: UUID,
    val status: String, val enrolledAt: Instant, val withdrawnAt: Instant?)
interface EnrollmentCommands {
    fun enroll(section: UUID, key: String?): CommittedOutcome
    fun withdraw(enrollment: UUID, key: String?): CommittedOutcome
}
/** Identity is taken from Authentication; no student parameter is accepted. */
interface OwnEnrollments {
    fun list(term: UUID, limit: Int = 100, offset: Int = 0): List<EnrollmentView>
    fun activeForSchedule(term: UUID): List<EnrollmentView>
}
