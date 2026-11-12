package org.example.sdu.registration.enrollment.internal

import jakarta.annotation.PostConstruct
import org.example.sdu.registration.authentication.api.Actor
import org.example.sdu.registration.authentication.api.CurrentActor
import org.example.sdu.registration.enrollment.api.*
import org.example.sdu.registration.shared.errors.ApiFailure
import org.example.sdu.registration.shared.idempotency.ClaimWait
import org.example.sdu.registration.shared.idempotency.Operation
import org.example.sdu.registration.shared.observability.EnrollmentMetrics
import org.springframework.boot.context.properties.ConfigurationProperties
import org.springframework.stereotype.Component
import org.springframework.stereotype.Service
import org.springframework.transaction.support.TransactionSynchronizationManager
import java.sql.SQLException
import java.util.UUID
import java.util.concurrent.Semaphore
import java.util.concurrent.ThreadLocalRandom
import java.util.concurrent.TimeUnit

@Component
@ConfigurationProperties("enrollment")
class EnrollmentSettings(private val environment: org.springframework.core.env.Environment) {
    var benchmarkEarlySectionLocking = false
    var admission = 64
    var jdbcAdmissionEnabled = false
    var jdbcAdmissionWaitMs = 3_000L
    val jdbcConcurrency: Int
        get() = (environment.getProperty("spring.datasource.hikari.maximum-pool-size", Int::class.java, 10) / 2)
            .coerceAtLeast(1).coerceAtMost(admission)
    var maxAttempts = 3
    var deadlineMs = 15_000L
    var lockTimeoutMs = 2_000L
    var statementTimeoutMs = 5_000L
    var retentionSeconds = 86_400L
    @PostConstruct fun validate() {
        require(!benchmarkEarlySectionLocking || environment.acceptsProfiles(org.springframework.core.env.Profiles.of("benchmark"))) { "Early-lock comparison is isolated to the benchmark profile" }
        require(admission in 1..512 && maxAttempts in 1..5)
        require(jdbcAdmissionWaitMs in 1..60_000)
        require(deadlineMs in 1_000..60_000 && lockTimeoutMs in 50..deadlineMs && statementTimeoutMs in lockTimeoutMs..deadlineMs)
        require(retentionSeconds >= 3_600 && retentionSeconds <= 604_800)
    }
}

@Service
@org.springframework.boot.autoconfigure.condition.ConditionalOnProperty(name = ["portal.write-behind.enabled"], havingValue = "false", matchIfMissing = true)
class EnrollmentService(private val current: CurrentActor, private val attempt: EnrollmentAttempt,
    private val settings: EnrollmentSettings, private val metrics: EnrollmentMetrics) : EnrollmentCommands {
    private val slots by lazy { Semaphore(settings.admission) }
    // Local capacity control only. The database locks and constraints coordinate all replicas.
    private val jdbcSlots by lazy { Semaphore(settings.jdbcConcurrency, true) }
    override fun enroll(section: UUID, key: String?): CommittedOutcome = submit("ENROLL", section, key)
    override fun withdraw(enrollment: UUID, key: String?): CommittedOutcome = submit("WITHDRAW", enrollment, key)
    private fun submit(type: String, target: UUID, key: String?): CommittedOutcome {
        check(!TransactionSynchronizationManager.isActualTransactionActive()) { "Retry orchestration must be outside a transaction" }
        val actor = current.student()
        if (key == null) throw ApiFailure(400, "IDEMPOTENCY_KEY_REQUIRED")
        if (!UUID_PATTERN.matches(key)) throw ApiFailure(400, "INVALID_REQUEST")
        val operation = Operation(actor.principalId, type, target, UUID.fromString(key))
        metrics.count("attempts")
        if (!slots.tryAcquire()) { metrics.count("technical", "SERVICE_OVERLOADED"); throw ApiFailure(503, "SERVICE_OVERLOADED", true) }
        val deadline = System.nanoTime() + settings.deadlineMs * 1_000_000
        try {
            for (number in 1..settings.maxAttempts) {
                try {
                    val result = executeWithAdmission(actor, operation, deadline)
                    when {
                        result.replayed -> metrics.count("replays")
                        result.status in 200..299 -> metrics.count("successes", type)
                        else -> metrics.count("denials", result.code)
                    }
                    return result
                } catch (failure: Exception) {
                    if (failure is ApiFailure) { if (failure.status >= 500 || failure.retryable) metrics.count("technical", failure.code); throw failure }
                    val state = generateSequence<Throwable>(failure) { it.cause }.filterIsInstance<SQLException>().firstOrNull()?.sqlState
                    if (state in setOf("40P01", "40001") && number < settings.maxAttempts && System.nanoTime() + 100_000_000 < deadline) {
                        metrics.count("retries", state)
                        Thread.sleep(ThreadLocalRandom.current().nextLong(5, 26))
                        continue
                    }
                    val code = when {
                        failure is ClaimWait && state in setOf("55P03", "57014") -> "REQUEST_IN_PROGRESS"
                        state?.startsWith("08") == true -> "OUTCOME_UNKNOWN"
                        state in setOf("55P03", "57014", "40P01", "40001") || failure is org.springframework.transaction.CannotCreateTransactionException -> "TEMPORARILY_UNAVAILABLE"
                        else -> "INTERNAL_ERROR"
                    }
                    metrics.count("technical", code)
                    throw ApiFailure(when(code) { "REQUEST_IN_PROGRESS" -> 409; "INTERNAL_ERROR" -> 500; else -> 503 }, code, code != "INTERNAL_ERROR", failure)
                }
            }
            error("Attempt budget exhausted")
        } finally { slots.release() }
    }

    private fun executeWithAdmission(actor: Actor,
        operation: Operation, deadline: Long): CommittedOutcome {
        if (!settings.jdbcAdmissionEnabled) return executeAttempt(actor, operation, deadline)
        val wait = metrics.timed("admission.wait") {
            val available = (deadline - System.nanoTime()).coerceAtMost(TimeUnit.MILLISECONDS.toNanos(settings.jdbcAdmissionWaitMs))
            try { available > 0 && jdbcSlots.tryAcquire(available, TimeUnit.NANOSECONDS) }
            catch (_: InterruptedException) {
                Thread.currentThread().interrupt()
                throw ApiFailure(503, "TEMPORARILY_UNAVAILABLE", true)
            }
        }
        if (!wait) throw ApiFailure(503, "SERVICE_OVERLOADED", true)
        try {
            // The proxy checks out a connection only after admission and commits before returning.
            return executeAttempt(actor, operation, deadline)
        } finally {
            jdbcSlots.release()
        }
    }

    private fun executeAttempt(actor: Actor, operation: Operation, deadline: Long): CommittedOutcome = metrics.traceTransaction(operation.type) {
        val start = System.nanoTime()
        try { attempt.execute(actor, operation, deadline, start) }
        finally { metrics.duration("transaction", start) }
    }
    companion object { private val UUID_PATTERN = Regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$") }
}
