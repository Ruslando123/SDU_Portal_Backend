package org.example.sdu.registration.studentportal.internal

import org.example.sdu.registration.authentication.api.CurrentActor
import org.example.sdu.registration.shared.database.Sql
import org.springframework.transaction.annotation.Transactional
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.RequestParam
import org.springframework.web.bind.annotation.RestController
import java.util.UUID

@RestController
class CatalogController(private val current: CurrentActor, private val sql: Sql, private val catalog: PublishedCatalog) {
    @GetMapping("/api/v1/terms")
    @Transactional(readOnly = true, timeout = 5)
    fun terms(): List<Map<String, Any?>> {
        val student = requireNotNull(current.student().studentId)
        return sql.rows("""
            SELECT t.term_id AS "termId", t.code, t.starts_on::text AS "startsOn", t.ends_on::text AS "endsOn",
                   t.timezone_name AS timezone, coalesce(st.credit_limit_override, p.credit_limit) AS "creditLimit"
            FROM catalog.academic_terms t
            JOIN rules.term_policy_gates g ON g.term_id=t.term_id AND g.configuration_state='PUBLISHED'
            LEFT JOIN enrollment.student_terms st ON st.term_id=t.term_id AND st.student_id=?
            LEFT JOIN rules.registration_policies p ON p.policy_id=st.policy_id AND p.term_id=t.term_id
            ORDER BY t.starts_on DESC, t.term_id LIMIT 100
        """, student)
    }

    @GetMapping("/api/v1/courses")
    fun courses(
        @RequestParam termId: UUID,
        @RequestParam(defaultValue = "") q: String,
        @RequestParam(defaultValue = "false") available: Boolean,
        @RequestParam(defaultValue = "24") limit: Int,
        @RequestParam(defaultValue = "0") offset: Int,
    ): Map<String, Any?> {
        current.student()
        require(q.length <= 100 && limit in 1..100 && offset in 0..100_000)
        val search = q.trim()
        val rows = if (catalog.hasReplica && !available) catalog.stale(termId, search, limit + 1, offset)
            else catalog.primary(termId, search, available, limit + 1, offset)
        return mapOf("items" to rows.take(limit), "hasMore" to (rows.size > limit))
    }
}
