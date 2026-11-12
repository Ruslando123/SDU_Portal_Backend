package org.example.sdu.registration.shared.database

import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Component
import java.math.BigDecimal
import java.time.Instant
import java.time.OffsetDateTime
import java.time.ZoneOffset
import java.util.UUID

typealias Row = Map<String, Any?>
fun Row.uuid(key: String): UUID = this[key] as UUID
fun Row.text(key: String): String = this[key] as String
fun Row.decimal(key: String): BigDecimal = this[key] as BigDecimal
fun Row.instant(key: String): Instant = when (val value = this[key]) {
    is java.sql.Timestamp -> value.toInstant()
    is OffsetDateTime -> value.toInstant()
    else -> error("Expected database timestamp")
}

/** All calls use Spring's transaction-bound connection; no connection is opened manually. */
@Component
class Sql(private val jdbc: JdbcTemplate, private val metrics: org.example.sdu.registration.shared.observability.EnrollmentMetrics) {
    private fun parameters(args: Array<out Any?>) = args.map {
        if (it is Instant) OffsetDateTime.ofInstant(it, ZoneOffset.UTC) else it
    }.toTypedArray()
    fun rows(query: String, vararg args: Any?): List<Row> = metrics.sql("select") { jdbc.queryForList(query, *parameters(args)) }
    fun one(query: String, vararg args: Any?): Row? = rows(query, *args).singleOrNull()
    fun update(query: String, vararg args: Any?): Int = metrics.sql(if (query.trimStart().startsWith("INSERT", true)) "insert" else if (query.trimStart().startsWith("UPDATE", true)) "update" else "other") { jdbc.update(query, *parameters(args)) }
    fun clock(): Instant = requireNotNull(one("SELECT clock_timestamp() AS time")).instant("time")
}
