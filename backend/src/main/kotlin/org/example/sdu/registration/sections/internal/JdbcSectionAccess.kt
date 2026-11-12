package org.example.sdu.registration.sections.internal

import org.example.sdu.registration.sections.api.*
import org.example.sdu.registration.shared.database.*
import org.springframework.stereotype.Service
import org.springframework.transaction.annotation.Propagation
import org.springframework.transaction.annotation.Transactional
import java.util.UUID

data class PublishedSectionPage(val sections: List<Section>, val meetings: List<Meeting>)

@Service
@Transactional(propagation = Propagation.MANDATORY)
class JdbcSectionAccess(private val sql: Sql, private val live: org.springframework.beans.factory.ObjectProvider<LiveSeatView>) : SectionFacts, SectionAllocation {
    private fun section(row: Row) = Section(row.uuid("section_id"), row.uuid("offering_id"), row.uuid("term_id"), row["publication_status"] == "PUBLISHED", (row["capacity"] as Number).toInt(), (row["enrolled_count"] as Number).toInt())
    override fun locate(id: UUID): Section? = sql.one("SELECT * FROM sections.course_sections WHERE section_id=?", id)?.let(::section)
    override fun lock(id: UUID): Section? = sql.one("SELECT * FROM sections.course_sections WHERE section_id=? FOR UPDATE", id)?.let(::section)
    override fun allocate(id: UUID): Boolean = sql.update("UPDATE sections.course_sections SET enrolled_count=enrolled_count+1 WHERE section_id=? AND enrolled_count<capacity", id) == 1
    override fun release(id: UUID) { check(sql.update("UPDATE sections.course_sections SET enrolled_count=enrolled_count-1 WHERE section_id=? AND enrolled_count>0", id) == 1) { "Invalid release count" } }
    override fun meetings(ids: Set<UUID>): List<Meeting> {
        if (ids.isEmpty()) return emptyList()
        return sql.rows("SELECT * FROM sections.section_meetings WHERE section_id IN (${ids.joinToString { "?" }}) ORDER BY starts_at,meeting_id", *ids.toTypedArray())
            .map(::meeting)
    }
    private fun overlay(section: Section) = live.ifAvailable?.occupied(section.id)?.let { section.copy(count = it) } ?: section
    override fun published(offering: UUID, term: UUID, limit: Int, offset: Int): List<Section> = sql.rows("""
        SELECT s.* FROM sections.course_sections s
        WHERE s.offering_id=? AND s.term_id=? AND s.publication_status='PUBLISHED'
        ORDER BY s.section_id LIMIT ? OFFSET ?
    """, offering, term, limit, offset).map(::section).map(::overlay)

    fun publishedWithMeetings(offering: UUID, term: UUID, limit: Int, offset: Int): PublishedSectionPage {
        val rows = sql.rows("""
            SELECT page.*, m.meeting_id, m.starts_at, m.ends_at, m.is_required, m.meeting_kind, m.venue
            FROM (
                SELECT s.section_id, s.offering_id, s.term_id, s.publication_status, s.capacity, s.enrolled_count
                FROM sections.course_sections s
                WHERE s.offering_id=? AND s.term_id=? AND s.publication_status='PUBLISHED'
                ORDER BY s.section_id LIMIT ? OFFSET ?
            ) page LEFT JOIN sections.section_meetings m ON m.section_id=page.section_id
            ORDER BY page.section_id
        """, offering, term, limit, offset)
        val sections = rows.distinctBy { it.uuid("section_id") }.map(::section).map(::overlay)
        val meetings = rows.filter { it["meeting_id"] != null }.map(::meeting)
            .sortedWith(compareBy(Meeting::startsAt).thenBy { it.id.toString() })
        return PublishedSectionPage(sections, meetings)
    }

    private fun meeting(row: Row) = Meeting(row.uuid("meeting_id"), row.uuid("section_id"), row.instant("starts_at"),
        row.instant("ends_at"), row["is_required"] as Boolean, row.text("meeting_kind"), row["venue"] as String?)
}
