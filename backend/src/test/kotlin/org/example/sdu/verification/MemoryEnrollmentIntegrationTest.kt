package org.example.sdu.verification

import org.example.sdu.registration.enrollment.internal.memory.*
import org.example.sdu.registration.enrollment.api.OwnEnrollments
import org.example.sdu.registration.shared.errors.ApiFailure
import org.junit.jupiter.api.*
import org.junit.jupiter.api.Assertions.*
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Configuration
import org.springframework.context.annotation.Primary
import org.testcontainers.containers.GenericContainer
import org.testcontainers.containers.wait.strategy.Wait
import org.testcontainers.utility.DockerImageName
import java.time.Duration
import java.util.UUID
import java.util.concurrent.Callable
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.TimeoutException
import java.util.concurrent.atomic.AtomicBoolean

@Configuration(proxyBeanMethods = false)
class LostConfirmConfiguration {
    @Bean @Primary fun failureFactory(settings: MemorySettings): DecisionJournalFactory = DecisionJournalFactory {
        val real = RabbitJournal(settings)
        object : DecisionJournal by real {
            override fun publish(id: String, bytes: ByteArray) {
                real.publish(id, bytes)
                captured.add(bytes.copyOf())
                if (loseConfirm.compareAndSet(true, false)) throw TimeoutException("Injected lost confirm after durable acceptance")
            }
        }
    }
    companion object { val loseConfirm = AtomicBoolean(false); val captured = java.util.concurrent.CopyOnWriteArrayList<ByteArray>() }
}

@TestInstance(TestInstance.Lifecycle.PER_CLASS)
class MemoryEnrollmentIntegrationTest {
    private class Broker : GenericContainer<Broker>(DockerImageName.parse("rabbitmq:4.4.0@sha256:485319d42170eab53d8a030e1cc49d95e41f5d325479fa504736db8091981eb7"))
    private val password = UUID.randomUUID().toString()
    private val broker = Broker().withExposedPorts(5672).withEnv("RABBITMQ_DEFAULT_USER", "portal")
        .withEnv("RABBITMQ_DEFAULT_PASS", password).withEnv("RABBITMQ_DEFAULT_VHOST", "/portal")
        .waitingFor(Wait.forLogMessage(".*Server startup complete.*\n", 1).withStartupTimeout(Duration.ofSeconds(90)))
    private lateinit var app: Phase5Harness
    private lateinit var options: Array<String>
    private val engine get() = app.context.getBean(MemoryEnrollmentEngine::class.java)
    @BeforeAll fun brokerStart() { broker.start() }
    @AfterAll fun brokerStop() { broker.stop() }
    @BeforeEach fun open() {
        LostConfirmConfiguration.loseConfirm.set(false)
        LostConfirmConfiguration.captured.clear()
        options = arrayOf("--portal.write-behind.enabled=true", "--portal.write-behind.projector-mode=sql", "--portal.write-behind.synthetic-only=true",
            "--portal.write-behind.eager-start=false", "--portal.write-behind.tls=false", "--portal.write-behind.host=${broker.host}",
            "--portal.write-behind.port=${broker.getMappedPort(5672)}", "--portal.write-behind.password=$password",
            "--portal.write-behind.queue=test.${UUID.randomUUID()}", "--portal.write-behind.auto-project=false")
        app = Phase5Harness(*options, extraSource = LostConfirmConfiguration::class.java)
    }
    @AfterEach fun close() { app.close() }

    @Test fun `confirmed decisions are immediately visible in RAM and project atomically with replay`() {
        val f = Fixture().populate(app.db)
        val key = UUID.randomUUID()
        val enrolled = app.enroll(f, key = key)
        assertEquals(201, enrolled.status)
        assertEquals(0, app.db.count("SELECT count(*) FROM enrollment.enrollments").toInt())
        assertEquals(1, engine.occupied(f.section))
        assertEquals(1, app.asStudent(f) { app.context.getBean(OwnEnrollments::class.java).activeForSchedule(f.term).size })
        assertEquals(enrolled.body, app.enroll(f, key = key).body)
        assertEquals("IDEMPOTENCY_KEY_REUSED", assertThrows(ApiFailure::class.java) { app.enroll(f, UUID.randomUUID(), key) }.code)
        val withdrawn = app.withdraw(f, app.id(enrolled))
        assertEquals(200, withdrawn.status)
        assertEquals(0, engine.occupied(f.section))
        assertEquals(2, engine.drain())
        assertEquals(1L, app.db.count("SELECT count(*) FROM enrollment.enrollments WHERE status='WITHDRAWN'"))
        assertEquals(2L, app.db.count("SELECT count(*) FROM audit.audit_events"))
        app.db.invariants()
    }

    @Test fun `last seat is allocated once across competing students`() {
        val first = Fixture().populate(app.db, 1)
        val students = listOf(first) + (1..9).map { first.anotherStudent(app.db) }
        engine.start()
        val pool = Executors.newFixedThreadPool(10)
        try {
            val results = pool.invokeAll(students.map { student -> Callable { app.enroll(student) } }).map { it.get(15, TimeUnit.SECONDS) }
            assertEquals(1, results.count { it.code == "ENROLLED" })
            assertEquals(9, results.count { it.code == "SECTION_FULL" })
            assertEquals(1, engine.occupied(first.section))
            assertEquals(10, engine.drain())
            app.db.invariants()
        } finally { pool.shutdownNow() }
    }

    @Test fun `student academic checks and ownership remain authoritative before projection`() {
        val f = Fixture().populate(app.db)
        val other = f.anotherStudent(app.db)
        val conflicting = f.anotherOffering(app.db, conflict = true)
        val distributed = f.anotherOffering(app.db)
        app.db.connection().use { it.execute("UPDATE rules.registration_policies SET credit_limit=3 WHERE policy_id=?", f.policy) }
        val enrolled = app.enroll(f)
        assertEquals("ALREADY_ENROLLED", app.enroll(f).code)
        assertEquals("CREDIT_LIMIT_EXCEEDED", app.enroll(f, distributed.section).code)
        assertEquals("CREDIT_LIMIT_EXCEEDED", app.enroll(f, conflicting.section).code)
        assertEquals("RESOURCE_NOT_FOUND", app.withdraw(other, app.id(enrolled)).code)
        assertEquals("WITHDRAWN", app.withdraw(f, app.id(enrolled)).code)
        assertEquals("ALREADY_WITHDRAWN", app.withdraw(f, app.id(enrolled)).code)
        engine.drain()
        app.db.invariants()
    }

    @Test fun `schedule conflict and closed enrollment window retain existing denial codes`() {
        val f = Fixture().populate(app.db)
        val conflicting = f.anotherOffering(app.db, conflict = true)
        val closed = Fixture().populate(app.db)
        app.db.connection().use { it.execute("UPDATE rules.registration_windows SET is_closed=true,closed_at=clock_timestamp() WHERE window_id=?", closed.enrollWindow) }
        assertEquals("ENROLLED", app.enroll(f).code)
        assertEquals("SCHEDULE_CONFLICT", app.enroll(f, conflicting.section).code)
        assertEquals("REGISTRATION_CLOSED", app.enroll(closed).code)
        engine.drain()
        app.db.invariants()
    }

    @Test fun `lost broker confirm closes ingress and recovery resolves the original idempotency key`() {
        val f = Fixture().populate(app.db)
        val key = UUID.randomUUID()
        LostConfirmConfiguration.loseConfirm.set(true)
        assertEquals("OUTCOME_UNKNOWN", assertThrows(ApiFailure::class.java) { app.enroll(f, key = key) }.code)
        assertEquals("TEMPORARILY_UNAVAILABLE", assertThrows(ApiFailure::class.java) { app.enroll(f) }.code)
        assertEquals(0L, app.db.count("SELECT count(*) FROM enrollment.enrollments"))
        engine.close()
        app.replica(*options).use { recovered ->
            val restored = recovered.getBean(MemoryEnrollmentEngine::class.java)
            restored.start()
            val result = app.asStudent(f) { restored.enroll(f.section, key.toString()) }
            assertTrue(result.replayed)
            assertEquals(201, result.status)
            assertEquals(1L, app.db.count("SELECT count(*) FROM enrollment.enrollments"))
            app.asStudent(f) { restored.withdraw(app.id(result), UUID.randomUUID().toString()) }
            restored.drain()
            app.db.invariants()
        }
    }

    @Test fun `restart drains acknowledged business decisions before serving its rebuilt state`() {
        val f = Fixture().populate(app.db)
        val key = UUID.randomUUID()
        val created = app.enroll(f, key = key)
        engine.close()
        app.replica(*options).use { recovered ->
            val restored = recovered.getBean(MemoryEnrollmentEngine::class.java)
            restored.start()
            assertEquals(1, restored.occupied(f.section))
            assertEquals(created.body, app.asStudent(f) { restored.enroll(f.section, key.toString()) }.body)
            assertEquals(1L, app.db.count("SELECT count(*) FROM audit.audit_events"))
            app.db.invariants()
        }
    }

    @Test fun `commit before consumer ACK can be delivered again without duplicate effects`() {
        val f = Fixture().populate(app.db)
        val created = app.enroll(f)
        // A separate connection can inspect the existing queue; the owner remains the sole projector.
        val settings = app.context.getBean(MemorySettings::class.java)
        val connection = com.rabbitmq.client.ConnectionFactory().apply {
            host = settings.host; port = settings.port; username = settings.username; password = settings.password; virtualHost = settings.virtualHost
        }.newConnection()
        val channel = connection.createChannel()
        val delivery = channel.basicGet(settings.queue, false)
        app.context.getBean(MemoryProjector::class.java).project(delivery.body)
        connection.abort() // COMMIT succeeded, ACK was lost: RabbitMQ requeues it.
        engine.close()
        app.replica(*options).use { recovered ->
            recovered.getBean(MemoryEnrollmentEngine::class.java).start()
            assertEquals(1L, app.db.count("SELECT count(*) FROM audit.audit_events"))
            assertEquals(1L, app.db.count("SELECT count(*) FROM enrollment.enrollments WHERE enrollment_id=?", app.id(created)))
            app.db.invariants()
        }
    }

    @Test fun `HTTP recovery preserves CSRF ownership and immediate account revocation`() {
        val f = Fixture().populate(app.db)
        val pass = UUID.randomUUID().toString()
        app.db.connection().use { it.execute("UPDATE identity.test_accounts SET password_hash=? WHERE principal_id=?",
            "{bcrypt}" + org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder(4).encode(pass), f.principal) }
        val http = java.net.http.HttpClient.newHttpClient()
        fun send(path: String, method: String = "GET", cookie: String? = null, csrf: String? = null, key: String? = null, form: String? = null): java.net.http.HttpResponse<String> {
            val request = java.net.http.HttpRequest.newBuilder(java.net.URI.create(app.base + path)).timeout(Duration.ofSeconds(8))
            cookie?.let { request.header("Cookie", it) }; csrf?.let { request.header("X-CSRF-TOKEN", it) }; key?.let { request.header("Idempotency-Key", it) }
            if (form != null) request.header("Content-Type", "application/x-www-form-urlencoded")
            request.method(method, form?.let { java.net.http.HttpRequest.BodyPublishers.ofString(it) } ?: java.net.http.HttpRequest.BodyPublishers.noBody())
            return http.send(request.build(), java.net.http.HttpResponse.BodyHandlers.ofString())
        }
        fun cookie(response: java.net.http.HttpResponse<String>) = response.headers().allValues("Set-Cookie").single { it.startsWith("SESSION=") }.substringBefore(';')
        fun csrf(response: java.net.http.HttpResponse<String>) = app.mapper.readTree(response.body()).get("token").asString()
        val initial = send("/api/v1/auth/csrf")
        val login = send("/api/v1/auth/login", "POST", cookie(initial), csrf(initial), form = "username=test-${f.principal}&password=$pass")
        assertEquals(200, login.statusCode())
        val session = cookie(login)
        val token = csrf(send("/api/v1/auth/csrf", cookie = session))
        val path = "/api/v1/sections/${f.section}/enrollments"
        assertEquals(401, send("/api/v1/me").statusCode())
        assertEquals(403, send(path, "POST", session, key = UUID.randomUUID().toString()).statusCode())
        val key = UUID.randomUUID().toString()
        val created = send(path, "POST", session, token, key)
        assertEquals(201, created.statusCode(), created.body())
        val replay = send(path, "POST", session, token, key)
        assertEquals(created.body(), replay.body())
        assertEquals("true", replay.headers().firstValue("Idempotency-Replayed").orElseThrow())
        val list = send("/api/v1/me/enrollments?termId=${f.term}", cookie = session)
        assertTrue(list.body().contains(app.mapper.readTree(created.body()).get("enrollmentId").asString()))
        app.db.connection().use { it.execute("UPDATE identity.test_accounts SET enabled=false WHERE principal_id=?", f.principal) }
        assertEquals(401, send(path, "POST", session, token, key).statusCode())
        engine.drain()
        assertEquals(1L, app.db.count("SELECT count(*) FROM operations.idempotency_records"))
        app.db.invariants()
    }

    @Test fun `eager recovery completes before concurrent SQL readers even with a two connection pool`() {
        lateinit var f: Fixture
        Phase5Harness(*options.filterNot { it.startsWith("--portal.write-behind.eager-start=") }.toTypedArray(), "--portal.write-behind.eager-start=true", "--spring.datasource.hikari.maximum-pool-size=2",
            prepare = { db -> f = Fixture().populate(db) }).use { readyApp ->
            val readyEngine = readyApp.context.getBean(MemoryEnrollmentEngine::class.java)
            assertEquals("READY", readyEngine.status()["state"])
            val transaction = org.springframework.transaction.support.TransactionTemplate(readyApp.context.getBean(org.springframework.transaction.PlatformTransactionManager::class.java))
            val facts = readyApp.context.getBean(org.example.sdu.registration.sections.api.SectionFacts::class.java)
            val pool = Executors.newFixedThreadPool(10)
            try {
                val results = pool.invokeAll((1..10).map { Callable { transaction.execute { facts.published(f.offering, f.term, 10, 0) } } })
                results.forEach { assertEquals(0, it.get(5, TimeUnit.SECONDS)!!.single().count) }
            } finally { pool.shutdownNow() }
        }
    }

    @Test fun `a second owner is rejected and original owner remains usable`() {
        val f = Fixture().populate(app.db)
        engine.start()
        app.replica(*options).use { second ->
            assertThrows(IllegalStateException::class.java) { second.getBean(MemoryEnrollmentEngine::class.java).start() }
        }
        assertEquals(201, app.enroll(f).status)
        engine.drain()
        app.db.invariants()
    }
    @Test fun `SQL projector rolls back seat episode claim and audit together on a late failure`() {
        val f = Fixture().populate(app.db)
        app.enroll(f)
        val body = LostConfirmConfiguration.captured.single()
        val projector = app.context.getBean(MemoryProjector::class.java)
        app.db.connection().use { c ->
            c.execute("CREATE FUNCTION audit.inject_projection_failure() RETURNS trigger LANGUAGE plpgsql AS 'BEGIN RAISE EXCEPTION ''injected late audit failure''; END'")
            c.execute("CREATE TRIGGER injected_failure BEFORE INSERT ON audit.audit_events FOR EACH ROW EXECUTE FUNCTION audit.inject_projection_failure()")
        }
        assertThrows(Exception::class.java) { projector.project(body) }
        assertEquals(0L, app.db.count("SELECT count(*) FROM enrollment.enrollments"))
        assertEquals(0L, app.db.count("SELECT count(*) FROM operations.idempotency_records"))
        assertEquals(0L, app.db.count("SELECT sum(enrolled_count) FROM sections.course_sections"))
        app.db.connection().use { it.execute("DROP TRIGGER injected_failure ON audit.audit_events") }
        assertEquals(1, engine.drain()) // Failed SQL did not remove the confirmed broker event.
        app.db.invariants()
    }

    @Test fun `SQL inbox survives idempotency expiry and rejects altered redelivery`() {
        val f = Fixture().populate(app.db)
        app.enroll(f)
        val bytes = LostConfirmConfiguration.captured.single()
        engine.drain()
        app.db.connection().use { it.execute("DELETE FROM operations.idempotency_records") }
        val projector = app.context.getBean(MemoryProjector::class.java)
        projector.project(bytes)
        assertEquals(1L, app.db.count("SELECT count(*) FROM enrollment.enrollments"))
        assertEquals(1L, app.db.count("SELECT count(*) FROM audit.audit_events"))
        val event = MemoryEvent.decode(bytes, app.mapper)
        assertThrows(Exception::class.java) { projector.project(event.copy(body=event.body + ("extra" to "tampered")).encode(app.mapper)) }
        app.db.invariants()
    }

    @Test fun `SQL projector rejects out of order withdrawal and subsequently accepts original order`() {
        val f = Fixture().populate(app.db)
        val enrolled = app.enroll(f)
        app.withdraw(f, app.id(enrolled))
        val projector = app.context.getBean(MemoryProjector::class.java)
        assertThrows(Exception::class.java) { projector.project(LostConfirmConfiguration.captured[1]) }
        assertEquals(0L, app.db.count("SELECT count(*) FROM operations.idempotency_records"))
        assertEquals(2, engine.drain())
        assertEquals(0L, app.db.count("SELECT count(*) FROM enrollment.enrollments WHERE status='ACTIVE'"))
        app.db.invariants()
    }

    @Test fun `lost withdrawal response after restart replays original key without cancelling a new enrollment`() {
        val f = Fixture().populate(app.db)
        val enrolled = app.enroll(f)
        val key = UUID.randomUUID()
        val withdrawn = app.asStudent(f) { engine.withdraw(app.id(enrolled), key.toString()) }
        val next = app.enroll(f)
        engine.close()
        app.replica(*options).use { recovered ->
            val restored = recovered.getBean(MemoryEnrollmentEngine::class.java)
            restored.start()
            val replay = app.asStudent(f) { restored.withdraw(app.id(enrolled), key.toString()) }
            assertTrue(replay.replayed)
            assertEquals(withdrawn.body, replay.body)
            assertEquals(1L, app.db.count("SELECT count(*) FROM enrollment.enrollments WHERE enrollment_id=? AND status='ACTIVE'", app.id(next)))
            assertEquals(3L, app.db.count("SELECT count(*) FROM audit.audit_events"))
            app.db.invariants()
        }
    }

    @Test fun `single call projector and legacy path preserve outcomes with comparable local replay timings`() {
        val f = Fixture().populate(app.db)
        repeat(120) { val enrolled=app.enroll(f); app.withdraw(f, app.id(enrolled)) }
        val bytes=LostConfirmConfiguration.captured.toList()
        val settings=app.context.getBean(MemorySettings::class.java)
        val projector=app.context.getBean(MemoryProjector::class.java)
        val times=mutableMapOf<String,Double>()
        // Use disjoint sequential halves of the same student's enroll-withdraw history.
        listOf("jdbc", "sql").forEachIndexed { index, mode ->
            settings.projectorMode=mode
            val begin=System.nanoTime()
            bytes.drop(index*120).take(120).forEach(projector::project)
            times[mode]=(System.nanoTime()-begin)/1e6
            assertEquals(0L, app.db.count("SELECT count(*) FROM enrollment.enrollments WHERE status='ACTIVE'"))
        }
        println("MEMORY_PROJECTOR_LOCAL_120_EVENTS_MS $times")
        settings.projectorMode="sql"
        assertEquals(240,engine.drain()) // Entire committed journal can be redelivered without new side effects.
        assertEquals(240L,app.db.count("SELECT count(*) FROM audit.audit_events"))
        app.db.invariants()
    }

}
