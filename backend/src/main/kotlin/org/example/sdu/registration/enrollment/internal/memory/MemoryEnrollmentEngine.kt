package org.example.sdu.registration.enrollment.internal.memory

import io.micrometer.core.instrument.MeterRegistry
import org.example.sdu.registration.academicrules.api.*
import org.example.sdu.registration.authentication.api.CurrentActor
import org.example.sdu.registration.enrollment.api.*
import org.example.sdu.registration.shared.database.LiveSeatView
import org.example.sdu.registration.shared.errors.ApiFailure
import org.example.sdu.registration.shared.idempotency.*
import org.slf4j.MDC
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.stereotype.Service
import tools.jackson.databind.json.JsonMapper
import java.math.BigDecimal
import java.sql.Connection
import java.time.Instant
import java.util.UUID
import java.util.concurrent.Semaphore
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.locks.ReentrantLock
import javax.sql.DataSource
import kotlin.concurrent.withLock

/** Experimental single decision owner. All student/seat changes become visible together after broker confirm. */
@Service
@ConditionalOnProperty(name = ["portal.write-behind.enabled"], havingValue = "true")
class MemoryEnrollmentEngine(private val current: CurrentActor, private val settings: MemorySettings,
    private val loader: MemorySnapshot, private val projector: MemoryProjector, private val datasource: DataSource,
    private val mapper: JsonMapper, private val metrics: MeterRegistry, private val factory: DecisionJournalFactory) : EnrollmentCommands, OwnEnrollments, LiveSeatView, AutoCloseable {
    private val lock = ReentrantLock(true)
    private val admission = Semaphore(64)
    private val pending = AtomicInteger()
    @Volatile private var phase = "NEW"
    @Volatile private var worker: Thread? = null
    private var journal: DecisionJournal? = null
    private var lease: Connection? = null
    private lateinit var state: MemoryState
    private var started = 0L
    private var decisions = 0
    init {
        metrics.gauge("portal.memory.projector.sql", settings) { if (it.projectorMode == "sql") 1.0 else 0.0 }
        metrics.gauge("portal.memory.pending", pending)
        metrics.gauge("portal.memory.ready", this) { if (it.phase == "READY") 1.0 else 0.0 }
    }
    @org.springframework.context.event.EventListener(org.springframework.boot.context.event.ApplicationReadyEvent::class)
    fun initializeBeforeReadiness() { if (settings.eagerStart) start() }
    fun start() = lock.withLock {
        if (phase == "READY") return@withLock
        check(phase == "NEW") { "Recovery requires a new application process" }
        phase = "RECOVERING"
        try {
            lease = datasource.connection.also { connection ->
                connection.autoCommit = false
                connection.createStatement().use { statement ->
                    statement.queryTimeout = 5
                    statement.executeQuery("SELECT pg_try_advisory_xact_lock(735139820541::bigint)").use { check(it.next() && it.getBoolean(1)) { "Another memory owner is running" } }
                    // This bounded experiment freezes academic configuration and assignment changes.
                    statement.executeQuery("SELECT term_id FROM rules.term_policy_gates ORDER BY term_id FOR SHARE").use { while (it.next()) Unit }
                }
            }
            journal = factory.open()
            while (requireNotNull(journal).projectOne(projector::project)) Unit
            state = loader.load()
            check(state.outcomes.size < settings.maxDecisions)
            started = System.nanoTime()
            phase = "READY"
            if (settings.autoProject) worker = Thread({
                try {
                    while (phase == "READY") {
                        if (requireNotNull(journal).projectOne { event ->
                            val began = System.nanoTime()
                            projector.project(event)
                            metrics.timer("portal.memory.projection").record(System.nanoTime() - began, TimeUnit.NANOSECONDS)
                        }) {
                            pending.decrementAndGet()
                            metrics.counter("portal.memory.projected").increment()
                        } else Thread.sleep(10)
                    }
                } catch (_: InterruptedException) { Thread.currentThread().interrupt() }
                catch (_: Exception) { phase = "FAILED"; metrics.counter("portal.memory.projection.failures").increment() }
            }, "memory-sql-projector").also { it.isDaemon = true; it.start() }
        } catch (failure: Throwable) {
            phase = "FAILED"
            release()
            throw failure
        }
    }
    private fun ready() {
        if (phase == "NEW") start()
        if (phase != "READY" || journal?.healthy != true || System.nanoTime() - started >= settings.maxRuntimeSeconds * 1_000_000_000) {
            phase = "FAILED"
            throw ApiFailure(503, "TEMPORARILY_UNAVAILABLE", true)
        }
    }
    override fun enroll(section: UUID, key: String?) = submit("ENROLL", section, key)
    override fun withdraw(enrollment: UUID, key: String?) = submit("WITHDRAW", enrollment, key)
    private fun submit(type: String, target: UUID, key: String?): CommittedOutcome {
        val actor = current.student()
        if (key == null) throw ApiFailure(400, "IDEMPOTENCY_KEY_REQUIRED")
        if (!key.matches(Regex("[a-fA-F0-9]{8}(-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}"))) throw ApiFailure(400, "INVALID_REQUEST")
        val op = Operation(actor.principalId, type, target, UUID.fromString(key))
        if (!admission.tryAcquire()) throw ApiFailure(503, "SERVICE_OVERLOADED", true)
        try {
            val waiting = System.nanoTime()
            val acquired = lock.tryLock(5, TimeUnit.SECONDS)
            metrics.timer("portal.memory.lock.wait").record(System.nanoTime() - waiting, TimeUnit.NANOSECONDS)
            if (!acquired) throw ApiFailure(503, "SERVICE_OVERLOADED", true)
            val holding = System.nanoTime()
            try {
                ready()
                val replayKey = Triple(op.principal, op.type, op.key)
                state.outcomes[replayKey]?.let {
                    if (it.operation != op) throw ApiFailure(409, "IDEMPOTENCY_KEY_REUSED")
                    return it.outcome.copy(replayed = true)
                }
                if (pending.get() >= settings.maxPending || decisions + state.outcomes.size >= settings.maxDecisions) throw ApiFailure(503, "SERVICE_OVERLOADED", true)
                // Preserve the existing database clock semantics without a hot-path pool checkout.
                val time = try {
                    requireNotNull(lease).createStatement().use { statement ->
                        statement.queryTimeout = 3
                        statement.executeQuery("SELECT clock_timestamp()").use { check(it.next()); it.getTimestamp(1).toInstant() }
                    }
                } catch (failure: Exception) { phase = "FAILED"; throw ApiFailure(503, "TEMPORARILY_UNAVAILABLE", true, failure) }
                val event = decide(op, requireNotNull(actor.studentId), time)
                pending.incrementAndGet()
                try {
                    val began = System.nanoTime()
                    requireNotNull(journal).publish(event.id.toString(), event.encode(mapper))
                    metrics.timer("portal.memory.confirm").record(System.nanoTime() - began, TimeUnit.NANOSECONDS)
                } catch (failure: Exception) {
                    // The broker may have persisted the decision even when the confirm was lost.
                    phase = "FAILED"
                    throw ApiFailure(503, "OUTCOME_UNKNOWN", true, failure)
                }
                apply(event)
                decisions++
                val outcome = event.outcome(mapper)
                state.outcomes[replayKey] = MemoryReplay(op, outcome)
                metrics.counter("portal.memory.confirmed", "operation", type, "code", event.code).increment()
                return outcome
            } finally {
                metrics.timer("portal.memory.lock.hold").record(System.nanoTime() - holding, TimeUnit.NANOSECONDS)
                lock.unlock()
            }
        } finally { admission.release() }
    }
    private fun decide(op: Operation, student: UUID, time: Instant): MemoryEvent {
        val eventId = UUID.randomUUID()
        val correlation = MDC.get("correlationId") ?: UUID.randomUUID().toString()
        fun result(status: Int, code: String, episode: MemoryEpisode? = null, window: UUID? = null): MemoryEvent {
            val body = linkedMapOf<String, Any?>("code" to code, "decidedAt" to time.toString(), "correlationId" to correlation,
                "retryable" to false, "decisionAuthority" to "RABBITMQ_CONFIRMED")
            if (status >= 400) body["message"] = "The request was denied: $code."
            episode?.view?.let { body.putAll(mapOf("enrollmentId" to it.enrollmentId.toString(), "sectionId" to it.sectionId.toString(), "termId" to it.termId.toString(), "status" to it.status, "withdrawnAt" to it.withdrawnAt?.toString())) }
            return MemoryEvent(eventId, op, student, time, status, code, body, episode?.view?.enrollmentId,
                episode?.view?.sectionId, episode?.view?.termId, episode?.view?.offeringId, episode?.policy, window)
        }
        if (op.type == "WITHDRAW") {
            val episode = state.episodes[op.target]?.takeIf { it.student == student } ?: return result(404, "RESOURCE_NOT_FOUND")
            if (episode.view.termId !in state.publishedTerms) return result(409, "REGISTRATION_CLOSED")
            if (episode.view.status == "WITHDRAWN") return result(200, "ALREADY_WITHDRAWN", episode)
            val window = state.windows[episode.policy to "WITHDRAW"]
            if (window == null || !window.permits(time)) return result(409, "REGISTRATION_CLOSED")
            return result(200, "WITHDRAWN", episode.copy(view = episode.view.copy(status = "WITHDRAWN", withdrawnAt = time)), window.id)
        }
        val section = state.sections[op.target] ?: return result(404, "RESOURCE_NOT_FOUND")
        if (section.term !in state.publishedTerms) return result(409, "REGISTRATION_CLOSED")
        val assigned = state.students[student to section.term] ?: throw ApiFailure(503, "ELIGIBILITY_DATA_UNAVAILABLE", true)
        val offering = state.offerings.getValue(section.offering)
        val policy = state.policies.getValue(assigned.assignment.policy)
        if (!section.published || !offering.published) return result(404, "RESOURCE_NOT_FOUND")
        val window = state.windows[assigned.assignment.policy to "ENROLL"]
        if (window == null || !window.permits(time)) return result(409, "REGISTRATION_CLOSED")
        val active = state.episodes.values.filter { it.student == student && it.view.termId == section.term && it.view.status == "ACTIVE" }
        val existingSections = active.map { it.view.sectionId }.toSet()
        val candidateMeetings = state.meetings.filter { it.section == section.id && it.required }
        val existingMeetings = state.meetings.filter { it.section in existingSections && it.required }
        val checks = AcademicChecks(active.any { it.view.offeringId == section.offering }, assigned.evidence?.complete == true,
            assigned.evidence?.active == true, (section.offering to assigned.assignment.policy) in state.allowed,
            offering.prerequisites.all { prerequisite -> assigned.evidence?.completions?.any { completion ->
                completion.courseId == prerequisite.courseId && completion.passed && (prerequisite.minimumGrade == null ||
                    completion.scheme == prerequisite.scheme && completion.grade != null && completion.grade >= prerequisite.minimumGrade)
            } == true }, active.fold(BigDecimal.ZERO) { sum, episode -> sum + state.offerings.getValue(episode.view.offeringId).credits } + offering.credits,
            assigned.assignment.override ?: policy.limit,
            candidateMeetings.any { a -> existingMeetings.any { b -> a.startsAt < b.endsAt && b.startsAt < a.endsAt } })
        val denial = EvaluateEnrollmentRules.denial(checks)
        if (denial == "ELIGIBILITY_DATA_UNAVAILABLE") throw ApiFailure(503, denial, true)
        if (denial != null) return result(if (denial == "COHORT_INELIGIBLE") 403 else 409, denial)
        if (section.count >= section.capacity) return result(409, "SECTION_FULL")
        return result(201, "ENROLLED", MemoryEpisode(student, assigned.assignment.policy, EnrollmentView(UUID.randomUUID(), section.id,
            section.offering, section.term, "ACTIVE", time, null)), window.id)
    }
    private fun apply(event: MemoryEvent) {
        if (event.code == "ENROLLED") {
            val section = state.sections.getValue(requireNotNull(event.section))
            state.sections[section.id] = section.copy(count = section.count + 1)
            val id = requireNotNull(event.enrollment)
            state.episodes[id] = MemoryEpisode(event.student, requireNotNull(event.policy), EnrollmentView(id, section.id, section.offering, section.term, "ACTIVE", event.time, null))
        } else if (event.code == "WITHDRAWN") {
            val section = state.sections.getValue(requireNotNull(event.section))
            state.sections[section.id] = section.copy(count = section.count - 1)
            val episode = state.episodes.getValue(requireNotNull(event.enrollment))
            state.episodes[event.enrollment] = episode.copy(view = episode.view.copy(status = "WITHDRAWN", withdrawnAt = event.time))
        }
    }
    override fun list(term: UUID, limit: Int, offset: Int): List<EnrollmentView> {
        require(limit in 1..100 && offset in 0..100_000)
        val student = current.student().studentId
        return lock.withLock { ready(); state.episodes.values.filter { it.student == student && it.view.termId == term }.map { it.view }
            .sortedWith(compareByDescending<EnrollmentView> { it.enrolledAt }.thenBy { it.enrollmentId.toString() }).drop(offset).take(limit) }
    }
    override fun activeForSchedule(term: UUID): List<EnrollmentView> {
        val student = current.student().studentId
        return lock.withLock { ready(); state.episodes.values.filter { it.student == student && it.view.termId == term && it.view.status == "ACTIVE" }.map { it.view } }
    }
    override fun occupied(section: UUID): Int? = lock.withLock { ready(); state.sections[section]?.count }
    override fun availableOfferings(term: UUID): Set<UUID> = lock.withLock { ready(); state.sections.values.filter { it.term == term && it.published && it.count < it.capacity }.map { it.offering }.toSet() }
    /** Local control only, deliberately not an HTTP launch/admin endpoint. */
    fun drain(): Int = lock.withLock {
        ready()
        check(!settings.autoProject) { "Manual drain is test-only; automatic projector owns its channel" }
        var count = 0
        while (requireNotNull(journal).projectOne(projector::project)) { pending.decrementAndGet(); count++ }
        count
    }
    fun status(): Map<String, Any> = mapOf("state" to phase, "pending" to pending.get(), "confirmed" to decisions,
        "authority" to "rabbitmq-memory-v1", "durabilityScope" to "broker-storage-survival")
    override fun close() {
        phase = "STOPPED"
        worker?.interrupt()
        worker?.join(12_000)
        lock.withLock { release() }
    }
    private fun release() {
        try { journal?.close() } finally {
            try { lease?.rollback() } finally { lease?.close(); lease = null }
        }
    }
}
