package org.example.sdu.registration.catalog.api

import java.math.BigDecimal
import java.util.UUID

data class Prerequisite(val courseId: UUID, val minimumGrade: BigDecimal?, val scheme: String?)
data class Offering(val id: UUID, val term: UUID, val credits: BigDecimal, val published: Boolean, val prerequisites: List<Prerequisite>)
interface CatalogFacts {
    fun offering(id: UUID): Offering?
    fun credits(ids: Set<UUID>): Map<UUID, BigDecimal>
    fun titles(ids: Set<UUID>): Map<UUID, String>
    fun offeringForCourse(course: UUID, term: UUID): UUID?
    fun timezone(term: UUID): String?
}
