package org.example.sdu.registration.shared.observability

import io.micrometer.core.instrument.MeterRegistry
import jdk.jfr.Event
import jdk.jfr.Name
import org.slf4j.MDC
import org.springframework.beans.factory.annotation.Value
import org.springframework.stereotype.Component
import java.util.concurrent.TimeUnit

/** Best-effort diagnostics, deliberately outside the durable audit contract. Labels are bounded enums. */
@Component
class EnrollmentMetrics(private val registry: MeterRegistry,
    @Value("\${portal.benchmark.transaction-trace:false}") private val traceEnabled: Boolean,
) {
    private val trace = ThreadLocal<TransactionTimeline>()

    fun <T> traceTransaction(operation: String, action: () -> T): T {
        if (!traceEnabled) return action()
        val event = EnrollmentTransactionEvent()
        if (!event.isEnabled) return action()
        val timeline = TransactionTimeline(System.nanoTime())
        trace.set(timeline)
        event.begin()
        try {
            return action()
        } finally {
            trace.remove()
            event.end()
            if (event.shouldCommit()) {
                event.operation = operation
                event.correlationId = MDC.get(CorrelationIdFilter.MDC_KEY)
                event.sqlCalls = timeline.sqlCalls
                event.stages = timeline.stages.joinToString(";") { (name, offset, elapsed) -> "$name,$offset,$elapsed" }
                event.commit()
            }
        }
    }
    fun count(name: String, code: String? = null) {
        try {
            if (code == null) registry.counter("enrollment.$name").increment()
            else registry.counter("enrollment.$name", "code", code).increment()
        } catch (_: Exception) { /* Metrics never decide a commit. */ }
    }
    fun duration(name: String, start: Long) {
        val elapsed = System.nanoTime() - start
        if (traceEnabled && name in transactionStages) trace.get()?.let {
            it.stages.add(Triple(name, start - it.started, elapsed))
        }
        try { registry.timer("enrollment.$name").record(elapsed, TimeUnit.NANOSECONDS) }
        catch (_: Exception) { /* Metrics never decide a commit. */ }
    }
    fun <T> timed(name: String, action: () -> T): T {
        val start = System.nanoTime()
        return try { action() } finally { duration(name, start) }
    }
    fun <T> stage(stage: String, action: () -> T): T {
        val start = System.nanoTime()
        return try { action() } finally {
            try { registry.timer("enrollment.stage", "stage", stage).record(System.nanoTime() - start, TimeUnit.NANOSECONDS) }
            catch (_: Exception) { }
        }
    }
    fun <T> sql(operation: String, action: () -> T): T {
        if (traceEnabled) trace.get()?.let { it.sqlCalls++ }
        val start = System.nanoTime()
        return try { action() } finally {
            try { registry.timer("database.sql", "operation", operation).record(System.nanoTime() - start, TimeUnit.NANOSECONDS) }
            catch (_: Exception) { }
        }
    }
    fun untilCompletion(name: String, start: Long = System.nanoTime()) {
        org.springframework.transaction.support.TransactionSynchronizationManager.registerSynchronization(
            object : org.springframework.transaction.support.TransactionSynchronization {
                override fun afterCompletion(status: Int) { duration(name, start) }
            })
    }
    fun observeCommit() {
        var start = 0L
        org.springframework.transaction.support.TransactionSynchronizationManager.registerSynchronization(
            object : org.springframework.transaction.support.TransactionSynchronization {
                override fun beforeCommit(readOnly: Boolean) { start = System.nanoTime() }
                override fun afterCompletion(status: Int) { if (start != 0L) duration("commit", start) }
            })
    }

    private class TransactionTimeline(val started: Long) {
        val stages = mutableListOf<Triple<String, Long, Long>>()
        var sqlCalls = 0
    }

    companion object {
        private val transactionStages = setOf("connection.begin.enroll", "connection.begin.withdraw", "idempotency.claim",
            "student.lock.wait", "academic.checks", "section.lock.wait", "section.lock.hold", "decision.clock",
            "decision.window", "withdraw.recheck", "seat.write", "episode.write", "audit.write", "idempotency.complete", "commit")
    }
}

@Name("sdu.EnrollmentTransaction")
class EnrollmentTransactionEvent : Event() {
    @JvmField var operation: String? = null
    @JvmField var correlationId: String? = null
    @JvmField var sqlCalls = 0
    @JvmField var stages: String? = null
}
