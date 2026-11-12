package org.example.sdu.registration.enrollment.internal

import org.example.sdu.registration.authentication.api.CurrentActor
import org.example.sdu.registration.enrollment.api.*
import org.springframework.stereotype.Service
import org.springframework.transaction.annotation.Transactional
import java.util.UUID

@Service
@org.springframework.boot.autoconfigure.condition.ConditionalOnProperty(name = ["portal.write-behind.enabled"], havingValue = "false", matchIfMissing = true)
@Transactional(readOnly = true)
class EnrollmentQueries(private val current: CurrentActor, private val repository: EnrollmentRepository) : OwnEnrollments {
    override fun list(term: UUID, limit: Int, offset: Int): List<EnrollmentView> {
        require(limit in 1..100 && offset in 0..100_000)
        return repository.list(requireNotNull(current.student().studentId), term, limit, offset)
    }
    override fun activeForSchedule(term: UUID): List<EnrollmentView> = repository.active(requireNotNull(current.student().studentId), term).map { it.view }
}
