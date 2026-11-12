package org.example.sdu.registration.catalog.internal

import org.example.sdu.registration.catalog.api.*
import org.example.sdu.registration.shared.database.*
import org.springframework.stereotype.Service
import org.springframework.transaction.annotation.Propagation
import org.springframework.transaction.annotation.Transactional
import java.math.BigDecimal
import java.util.UUID

@Service
@Transactional(propagation = Propagation.MANDATORY)
class JdbcCatalogFacts(private val sql: Sql) : CatalogFacts {
    override fun offering(id: UUID): Offering? {
        val row = sql.one("SELECT offering_id,term_id,credits,publication_status FROM catalog.course_offerings WHERE offering_id=?", id) ?: return null
        val requirements = sql.rows("SELECT required_course_id,minimum_grade,grade_scheme FROM catalog.offering_prerequisites WHERE offering_id=?", id)
            .map { Prerequisite(it.uuid("required_course_id"), it["minimum_grade"] as BigDecimal?, it["grade_scheme"] as String?) }
        return Offering(id, row.uuid("term_id"), row.decimal("credits"), row["publication_status"] == "PUBLISHED", requirements)
    }
    override fun credits(ids: Set<UUID>): Map<UUID, BigDecimal> {
        if (ids.isEmpty()) return emptyMap()
        return sql.rows("SELECT offering_id,credits FROM catalog.course_offerings WHERE offering_id IN (${ids.joinToString { "?" }})", *ids.toTypedArray())
            .associate { it.uuid("offering_id") to it.decimal("credits") }
    }
    override fun titles(ids: Set<UUID>): Map<UUID, String> {
        if (ids.isEmpty()) return emptyMap()
        return sql.rows("SELECT offering_id,title FROM catalog.course_offerings WHERE offering_id IN (${ids.joinToString { "?" }})", *ids.toTypedArray())
            .associate { it.uuid("offering_id") to it.text("title") }
    }
    override fun offeringForCourse(course: UUID, term: UUID): UUID? = sql.one("SELECT offering_id FROM catalog.course_offerings WHERE course_id=? AND term_id=? AND publication_status='PUBLISHED'", course, term)?.uuid("offering_id")
    override fun timezone(term: UUID): String? = sql.one("SELECT timezone_name FROM catalog.academic_terms WHERE term_id=?", term)?.text("timezone_name")
}
