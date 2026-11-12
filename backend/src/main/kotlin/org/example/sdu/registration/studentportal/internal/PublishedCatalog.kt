package org.example.sdu.registration.studentportal.internal

import com.zaxxer.hikari.HikariConfig
import com.zaxxer.hikari.HikariDataSource
import org.example.sdu.registration.shared.database.Row
import org.example.sdu.registration.shared.database.Sql
import org.example.sdu.registration.shared.errors.ApiFailure
import org.springframework.core.env.Environment
import org.springframework.dao.DataAccessException
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Service
import org.springframework.transaction.annotation.Transactional
import java.util.UUID

/** The optional reader has its own credentials and does not register a second primary DataSource. */
@Service
class PublishedCatalog(private val sql: Sql, environment: Environment, private val live: org.springframework.beans.factory.ObjectProvider<org.example.sdu.registration.shared.database.LiveSeatView>) : AutoCloseable {
    private val replica = environment.getProperty("SDU_CATALOG_REPLICA_URL")?.takeIf(String::isNotBlank)?.let { url ->
        HikariDataSource(HikariConfig().apply {
            jdbcUrl = url
            username = environment.getRequiredProperty("SDU_CATALOG_REPLICA_USER")
            password = environment.getRequiredProperty("SDU_CATALOG_REPLICA_PASSWORD")
            poolName = "sdu-catalog-replica"
            maximumPoolSize = 4
            minimumIdle = 0
            connectionTimeout = 1_500
            initializationFailTimeout = -1
            isReadOnly = true
            addDataSourceProperty("connectTimeout", "2")
            addDataSourceProperty("socketTimeout", "3")
            // Fail checkout if a standby has been promoted or the route was misconfigured.
            connectionInitSql = "SELECT 1 / pg_is_in_recovery()::int"
        })
    }
    private val replicaJdbc = replica?.let { JdbcTemplate(it).apply { queryTimeout = 2 } }

    val hasReplica: Boolean get() = replicaJdbc != null

    @Transactional(readOnly = true, timeout = 5)
    fun primary(term: UUID, search: String, available: Boolean, limit: Int, offset: Int): List<Row> {
        val liveOfferings = if (available) live.ifAvailable?.availableOfferings(term) else null
        val openSeats = if (liveOfferings != null) "AND o.offering_id = ANY (CAST(? AS uuid[]))" else if (available) """
            AND EXISTS (
                SELECT 1 FROM sections.course_sections s WHERE s.term_id=o.term_id AND s.offering_id=o.offering_id
                    AND s.publication_status='PUBLISHED' AND s.enrolled_count<s.capacity
            )
        """ else ""
        return sql.rows("""
            SELECT c.course_id AS "courseId", o.offering_id AS "offeringId", c.code, o.title, o.credits
            FROM catalog.course_offerings o
            JOIN catalog.courses c ON c.course_id=o.course_id
            JOIN rules.term_policy_gates g ON g.term_id=o.term_id AND g.configuration_state='PUBLISHED'
            WHERE o.term_id=? AND o.publication_status='PUBLISHED'
              AND (strpos(lower(c.code), lower(?))>0 OR strpos(lower(o.title), lower(?))>0)
              $openSeats
            ORDER BY c.code, c.course_id LIMIT ? OFFSET ?
        """, *(listOf<Any>(term, search, search) + (liveOfferings?.let { listOf("{" + it.joinToString(",") + "}") } ?: emptyList()) + listOf(limit, offset)).toTypedArray())
    }

    fun stale(term: UUID, search: String, limit: Int, offset: Int): List<Row> {
        val jdbc = requireNotNull(replicaJdbc)
        return try {
            jdbc.queryForList("""
                SELECT course_id AS "courseId", offering_id AS "offeringId", code, title, credits
                FROM replica_catalog.published_courses
                WHERE term_id=? AND (strpos(lower(code), lower(?))>0 OR strpos(lower(title), lower(?))>0)
                  AND 1 / pg_is_in_recovery()::int = 1
                ORDER BY code, course_id LIMIT ? OFFSET ?
            """, term, search, search, limit, offset)
        } catch (failure: DataAccessException) {
            throw ApiFailure(503, "TEMPORARILY_UNAVAILABLE", true, failure)
        }
    }

    override fun close() { replica?.close() }
}
