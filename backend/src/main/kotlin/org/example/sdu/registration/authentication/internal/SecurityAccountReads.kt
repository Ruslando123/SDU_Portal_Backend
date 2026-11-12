package org.example.sdu.registration.authentication.internal

import com.zaxxer.hikari.HikariDataSource
import com.zaxxer.hikari.metrics.micrometer.MicrometerMetricsTrackerFactory
import io.micrometer.core.instrument.MeterRegistry
import jakarta.annotation.PostConstruct
import org.example.sdu.registration.shared.database.Sql
import org.example.sdu.registration.shared.observability.EnrollmentMetrics
import org.springframework.beans.factory.annotation.Value
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.context.annotation.Profile
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Component

/** Owns the optional pool without registering a DataSource or transaction manager for other modules. */
@Component
@Profile("benchmark")
@ConditionalOnProperty("portal.benchmark.security-pool-size")
class SecurityAccountReads(
    business: HikariDataSource,
    registry: MeterRegistry,
    metrics: EnrollmentMetrics,
    @Value("\${portal.benchmark.security-pool-size}") size: Int,
) : AutoCloseable {
    private val pool = HikariDataSource().apply {
        require(size in setOf(4, 6) && business.maximumPoolSize + size == 24) {
            "The security pool experiment requires a combined limit of 24 connections"
        }
        poolName = "sdu-security"
        jdbcUrl = business.jdbcUrl
        username = business.username
        password = business.password
        connectionTimeout = business.connectionTimeout
        validationTimeout = business.validationTimeout
        dataSourceProperties = business.dataSourceProperties
        addDataSourceProperty("ApplicationName", "sdu-security")
        addDataSourceProperty("readOnlyMode", "always")
        maximumPoolSize = size
        minimumIdle = size
        isReadOnly = true
        metricsTrackerFactory = MicrometerMetricsTrackerFactory(registry)
    }
    val sql = Sql(JdbcTemplate(pool), metrics)

    @PostConstruct
    fun start() {
        pool.connection.close()
    }

    override fun close() = pool.close()
}
