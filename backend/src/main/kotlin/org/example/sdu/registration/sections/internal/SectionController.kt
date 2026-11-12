package org.example.sdu.registration.sections.internal

import org.example.sdu.registration.authentication.api.CurrentActor
import org.example.sdu.registration.catalog.api.CatalogFacts
import org.example.sdu.registration.shared.database.Sql
import org.example.sdu.registration.shared.errors.ApiFailure
import org.springframework.beans.factory.annotation.Value
import org.springframework.stereotype.Service
import org.springframework.transaction.annotation.Isolation
import org.springframework.transaction.annotation.Transactional
import org.springframework.web.bind.annotation.*
import java.util.UUID

@Service
class PublishedSections(private val current: CurrentActor, private val facts: JdbcSectionAccess, private val catalog: CatalogFacts, private val sql: Sql,
    @Value("\${portal.performance.joined-section-meetings:false}") private val joinedSectionMeetings: Boolean,
) {
    @Transactional(readOnly = true, isolation = Isolation.REPEATABLE_READ, timeout = 5)
    fun list(course: UUID, term: UUID, limit: Int, offset: Int, includeInstructors: Boolean): Map<String, Any?> {
        current.student(); require(limit in 1..100 && offset in 0..100_000)
        val offering = catalog.offeringForCourse(course, term) ?: throw ApiFailure(404, "RESOURCE_NOT_FOUND")
        val page = if (joinedSectionMeetings) facts.publishedWithMeetings(offering, term, limit, offset) else {
            val sections = facts.published(offering, term, limit, offset)
            PublishedSectionPage(sections, facts.meetings(sections.map { it.id }.toSet()))
        }
        val snapshot = mutableMapOf<String, Any?>(
            "sections" to page.sections, "meetings" to page.meetings, "observedAt" to sql.clock(), "cache" to false,
        )
        if (includeInstructors) {
            val ids = page.sections.map { it.id }.toSet()
            snapshot["sectionDetails"] = if (ids.isEmpty()) emptyList<Map<String, Any?>>() else sql.rows("""
                SELECT s.section_id AS "sectionId", s.section_code AS code, i.display_name AS instructor
                FROM sections.course_sections s
                LEFT JOIN sections.section_instructors a ON a.section_id=s.section_id
                LEFT JOIN sections.instructors i ON i.instructor_id=a.instructor_id
                WHERE s.section_id IN (${ids.joinToString { "?" }})
                ORDER BY s.section_code, s.section_id, i.display_name
            """, *ids.toTypedArray())
        }
        return snapshot
    }
}
@RestController
class SectionController(private val query: PublishedSections) {
    @GetMapping("/api/v1/courses/{id}/sections")
    fun list(
        @PathVariable id: UUID, @RequestParam termId: UUID, @RequestParam(defaultValue = "100") limit: Int,
        @RequestParam(defaultValue = "0") offset: Int, @RequestParam(defaultValue = "false") includeInstructors: Boolean,
    ) = query.list(id, termId, limit, offset, includeInstructors)
}
