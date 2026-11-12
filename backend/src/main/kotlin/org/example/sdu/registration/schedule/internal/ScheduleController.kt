package org.example.sdu.registration.schedule.internal

import org.example.sdu.registration.enrollment.api.OwnEnrollments
import org.example.sdu.registration.catalog.api.CatalogFacts
import org.example.sdu.registration.sections.api.SectionFacts
import org.example.sdu.registration.shared.database.Sql
import org.example.sdu.registration.shared.errors.ApiFailure
import org.springframework.stereotype.Service
import org.springframework.transaction.annotation.Isolation
import org.springframework.transaction.annotation.Transactional
import org.springframework.web.bind.annotation.*
import java.math.BigDecimal
import java.util.UUID

@Service
class StudentSchedule(private val enrollments: OwnEnrollments, private val catalog: CatalogFacts, private val sections: SectionFacts, private val sql: Sql) {
    @Transactional(readOnly = true, isolation = Isolation.REPEATABLE_READ, timeout = 5)
    fun read(term: UUID, includeCourseTitles: Boolean): Map<String, Any?> {
        val active = enrollments.activeForSchedule(term)
        val timezone = catalog.timezone(term) ?: throw ApiFailure(404, "RESOURCE_NOT_FOUND")
        val offeringIds = active.map { it.offeringId }.toSet()
        val credits = catalog.credits(offeringIds)
        val schedule = mutableMapOf<String, Any?>("termId" to term, "timezone" to timezone, "observedAt" to sql.clock(),
            "credits" to credits.values.fold(BigDecimal.ZERO, BigDecimal::add), "enrollments" to active,
            "meetings" to sections.meetings(active.map { it.sectionId }.toSet()))
        if (includeCourseTitles) schedule["courseTitles"] = catalog.titles(offeringIds)
        return schedule
    }
}
@RestController
class ScheduleController(private val query: StudentSchedule) {
    @GetMapping("/api/v1/me/schedule")
    fun schedule(@RequestParam termId: UUID, @RequestParam(defaultValue = "false") includeCourseTitles: Boolean) =
        query.read(termId, includeCourseTitles)
}
