package org.example.sdu.verification

import org.junit.jupiter.api.AfterAll
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.BeforeAll
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.TestInstance
import org.springframework.boot.web.server.context.WebServerApplicationContext
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder
import org.springframework.session.SessionRepository
import org.springframework.session.jdbc.JdbcIndexedSessionRepository
import org.testcontainers.containers.GenericContainer
import org.testcontainers.containers.wait.strategy.Wait
import org.testcontainers.utility.DockerImageName
import org.testcontainers.utility.MountableFile
import java.net.URI
import java.net.http.HttpClient
import java.net.http.HttpRequest
import java.net.http.HttpResponse
import java.nio.file.Path
import java.time.Duration
import java.util.UUID
import java.util.concurrent.CompletableFuture

@TestInstance(TestInstance.Lifecycle.PER_CLASS)
class RedisSessionIntegrationTest {
    private class RedisContainer : GenericContainer<RedisContainer>(DockerImageName.parse("redis:7.0.15-bookworm"))
    private val password = UUID.randomUUID().toString() + "-secret"
    private val redis = RedisContainer().withExposedPorts(6379)
        .withCopyFileToContainer(MountableFile.forHostPath(Path.of(System.getProperty("project.root", ".."),
            "infrastructure/modal/start-redis-session.sh")), "/opt/start-redis-session.sh")
        .withEnv("PORTAL_SESSION_REDIS_PASSWORD", password)
        .withEnv("SDU_REDIS_OBSERVER_PASSWORD", UUID.randomUUID().toString() + "-secret")
        .withCommand("bash", "-ec", """
            work=/tmp/sdu-cloud
            mkdir -p -m 755 "${'$'}work"
            # Testcontainers reaches the container through its mapped port.
            sed 's/bind 127.0.0.1/bind 0.0.0.0/' /opt/start-redis-session.sh >/tmp/test-redis.sh
            source /tmp/test-redis.sh
            restart=true
            trap 'restart=true; kill -TERM "${'$'}redis_pid"; wait "${'$'}redis_pid" || true' USR1
            while [[ "${'$'}restart" == true ]]; do
              restart=false
              start_session_redis
              echo 'Session ACL ready'
              wait "${'$'}redis_pid" || true
            done
        """.trimIndent())
        .waitingFor(Wait.forLogMessage(".*Session ACL ready.*\n", 1))
    private lateinit var app: Phase5Harness
    private val client = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(3)).build()
    private val settings get() = arrayOf("--portal.session.store=redis", "--portal.session.redis.namespace=sdu:http-session:bench-v1",
        "--portal.session.redis.port=${redis.getMappedPort(6379)}", "--portal.session.redis.password=$password")

    @Test
    fun `both security pool splits retain live revocation even when the shared control is starved`(@org.junit.jupiter.api.io.TempDir evidence: Path) {
        val f = fixture()
        for (securitySize in listOf(0, 4, 6)) {
            val split = if (securitySize == 0) emptyArray() else arrayOf("--portal.benchmark.security-pool-size=$securitySize")
            app.replica(*settings, "--spring.profiles.active=test,benchmark", "--management.server.port=-1",
                "--portal.monitoring.enabled=false", "--portal.benchmark.transaction-trace=true",
                "--spring.datasource.hikari.maximum-pool-size=${24 - securitySize}", *split).use { candidate ->
                val pool = candidate.getBean(com.zaxxer.hikari.HikariDataSource::class.java)
                val security = candidate.getBeanProvider(org.example.sdu.registration.authentication.internal.SecurityAccountReads::class.java).ifAvailable
                if (securitySize == 0) assertNull(security)
                else assertEquals("on", security?.sql?.one("SHOW transaction_read_only")?.get("transaction_read_only"))
                assertEquals(24 - securitySize, pool.maximumPoolSize)
                val registry = candidate.getBean(io.micrometer.core.instrument.MeterRegistry::class.java)
                fun acquisitions(poolName: String) = registry.find("hikaricp.connections.acquire").tag("pool", poolName).timer()?.count() ?: 0
                val url = "http://127.0.0.1:${(candidate as WebServerApplicationContext).webServer!!.port}"
                val (session, csrf) = login(url, f)
                val write = "/api/v1/sections/${f.section}/enrollments"
                val businessBefore = acquisitions("sdu-runtime")
                val securityBefore = acquisitions("sdu-security")
                val key = UUID.randomUUID().toString()
                val jfr = evidence.resolve("enrollment-$securitySize.jfr")
                val enrolled = jdk.jfr.Recording().use { recording ->
                    recording.enable("sdu.EnrollmentTransaction")
                    recording.start()
                    send(url, write, "POST", session, csrf, key).also { recording.stop(); recording.dump(jfr) }
                }
                assertEquals(201, enrolled.statusCode(), enrolled.body())
                assertEquals(if (securitySize == 0) 2L else 1L, acquisitions("sdu-runtime") - businessBefore)
                assertEquals(if (securitySize == 0) 0L else 1L, acquisitions("sdu-security") - securityBefore)
                val trace = jdk.jfr.consumer.RecordingFile.readAllEvents(jfr).single { it.eventType.name == "sdu.EnrollmentTransaction" }
                assertEquals(enrolled.headers().firstValue("X-Correlation-ID").orElseThrow(), trace.getString("correlationId"))
                assertTrue(trace.getInt("sqlCalls") > 0)
                val stages = trace.getString("stages").split(';').map { it.substringBefore(',') }.toSet()
                assertTrue(stages.containsAll(listOf("section.lock.wait", "section.lock.hold", "audit.write", "idempotency.complete", "commit")))
                val id = app.mapper.readTree(enrolled.body()).get("enrollmentId").asString()
                assertEquals(200, send(url, "/api/v1/enrollments/$id", "DELETE", session, csrf, UUID.randomUUID().toString()).statusCode())

                val borrowed = mutableListOf<java.sql.Connection>()
                try {
                    repeat(pool.maximumPoolSize) { borrowed.add(pool.connection) }
                    assertEquals(if (securitySize == 0) 503 else 200, send(url, "/api/v1/me", cookie = session).statusCode())
                    app.db.connection().use { it.execute("UPDATE identity.test_accounts SET auth_version=auth_version+1 WHERE principal_id=?", f.principal) }
                    if (securitySize != 0) {
                        assertEquals(401, send(url, write, "POST", session, csrf, key).statusCode())
                        assertEquals(401, send(url, "/api/v1/me", cookie = session).statusCode())
                    }
                } finally {
                    borrowed.forEach { it.close() }
                }
                assertEquals(401, send(url, "/api/v1/me", cookie = session).statusCode())
            }
        }
        Phase5InvariantChecks.verify(app.db)
    }

    @Test
    fun `joined section read preserves HTTP access and uses one fewer SQL statement on the same checkouts`() {
        val f = fixture()
        val emptySection = UUID.randomUUID()
        val secondMeeting = UUID.randomUUID()
        app.db.connection().use { connection ->
            connection.execute("""
                INSERT INTO sections.course_sections
                    (section_id,offering_id,term_id,section_code,capacity,publication_status,published_at,config_version)
                VALUES (?,?,?,'B',30,'PUBLISHED',clock_timestamp(),1)
            """, emptySection, f.offering, f.term)
            connection.execute("""
                INSERT INTO sections.section_meetings
                SELECT ?,section_id,starts_at+interval '3 hours',ends_at+interval '3 hours',meeting_kind,is_required,venue
                FROM sections.section_meetings WHERE section_id=?
            """, secondMeeting, f.section)
        }
        app.replica(*settings, "--portal.performance.joined-section-meetings=true").use { candidate ->
            val baseline = app.context.getBean(io.micrometer.core.instrument.MeterRegistry::class.java)
            val joined = candidate.getBean(io.micrometer.core.instrument.MeterRegistry::class.java)
            val url = "http://127.0.0.1:${(candidate as WebServerApplicationContext).webServer!!.port}"
            val (session, csrf) = login(app.base, f)
            val path = "/api/v1/courses/${f.course}/sections?termId=${f.term}"
            fun counts(registry: io.micrometer.core.instrument.MeterRegistry) = listOf(
                registry.find("hikaricp.connections.acquire").timers().sumOf { it.count() },
                registry.find("database.sql").tag("operation", "select").timers().sumOf { it.count() },
            )
            fun fetch(base: String, registry: io.micrometer.core.instrument.MeterRegistry, sqlCount: Long) =
                counts(registry).let { before ->
                    val response = send(base, path, cookie = session)
                    assertEquals(200, response.statusCode(), response.body())
                    assertEquals(listOf(2L, sqlCount), counts(registry).zip(before) { after, previous -> after - previous })
                    app.mapper.readTree(response.body())
                }

            val control = fetch(app.base, baseline, 5L)
            val optimized = fetch(url, joined, 4L)
            assertEquals(2, optimized.get("sections").size())
            assertEquals(2, optimized.get("meetings").size())
            for (field in listOf("sections", "meetings", "cache")) assertEquals(control.get(field), optimized.get(field))
            fun checkouts(expected: Long, status: Int, request: () -> HttpResponse<String>): HttpResponse<String> {
                val before = counts(joined).first()
                val response = request()
                assertEquals(status, response.statusCode(), response.body())
                assertEquals(expected, counts(joined).first() - before)
                return response
            }
            for ((endpoint, expected) in listOf("/me" to 1L, "/me/enrollments?termId=${f.term}" to 2L,
                "/me/schedule?termId=${f.term}" to 2L)) {
                checkouts(expected, 200) { send(url, "/api/v1$endpoint", cookie = session) }
            }
            assertEquals(401, send(url, path).statusCode())
            assertEquals(400, send(url, "$path&limit=101", cookie = session).statusCode())
            assertEquals(404, send(url, "/api/v1/courses/${UUID.randomUUID()}/sections?termId=${f.term}", cookie = session).statusCode())
            for (suffix in listOf("&limit=1&offset=1&includeInstructors=true", "&offset=2&includeInstructors=true")) {
                val original = send(app.base, path + suffix, cookie = session)
                val result = send(url, path + suffix, cookie = session)
                assertEquals(200, result.statusCode(), result.body())
                for (field in listOf("sections", "meetings", "sectionDetails", "cache")) {
                    assertEquals(app.mapper.readTree(original.body()).get(field), app.mapper.readTree(result.body()).get(field))
                }
            }
            val enrollPath = "/api/v1/sections/${f.section}/enrollments"
            val key = UUID.randomUUID().toString()
            checkouts(1, 403) { send(url, enrollPath, "POST", session, null, key) }
            val enrollment = checkouts(2, 201) { send(url, enrollPath, "POST", session, csrf, key) }
            val replay = checkouts(2, 201) { send(url, enrollPath, "POST", session, csrf, key) }
            assertEquals(enrollment.body(), replay.body())
            assertEquals("true", replay.headers().firstValue("Idempotency-Replayed").orElseThrow())
            val id = app.mapper.readTree(enrollment.body()).get("enrollmentId").asString()
            checkouts(2, 200) { send(url, "/api/v1/enrollments/$id", "DELETE", session, csrf, UUID.randomUUID().toString()) }
            app.db.connection().use { it.execute("UPDATE identity.test_accounts SET enabled=false WHERE principal_id=?", f.principal) }
            assertEquals(401, send(url, "$path&limit=101", cookie = session).statusCode())
            app.db.connection().use { it.execute("UPDATE identity.test_accounts SET enabled=true WHERE principal_id=?", f.principal) }
            val (freshSession, _) = login(app.base, f)
            app.db.connection().use { it.execute("UPDATE identity.test_accounts SET auth_version=auth_version+1 WHERE principal_id=?", f.principal) }
            assertEquals(401, send(url, path, cookie = freshSession).statusCode())
            assertEquals(401, send(url, path, cookie = session).statusCode())
        }
    }

    @BeforeAll
    fun start() {
        redis.start()
        app = Phase5Harness(*settings, "--server.servlet.session.cookie.secure=true")
    }

    @AfterAll
    fun stop() {
        try { if (::app.isInitialized) app.close() } finally { redis.stop() }
    }

    @AfterEach
    fun invariants() = Phase5InvariantChecks.verify(app.db)

    @Test
    fun `redis only repository verifies security CSRF and durable retry across instances`() {
        val f = fixture()
        assertEquals(1, app.context.getBeansOfType(SessionRepository::class.java).size)
        assertTrue(app.context.getBeansOfType(JdbcIndexedSessionRepository::class.java).isEmpty())
        assertEquals(401, send(app.base, "/api/v1/me").statusCode())
        assertEquals(403, send(app.base, "/api/v1/auth/login", "POST", form = "username=invalid&password=invalid").statusCode())
        val (session, csrf) = login(app.base, f)
        val enroll = "/api/v1/sections/${f.section}/enrollments"
        assertEquals(403, send(app.base, enroll, "POST", session).statusCode())
        assertEquals(403, send(app.base, "/api/v1/admin/benchmark-authorize", cookie = session).statusCode())
        val key = UUID.randomUUID().toString()
        val first = send(app.base, enroll, "POST", session, csrf, key)
        assertEquals(201, first.statusCode(), first.body())

        app.replica(*settings).use { second ->
            val url = "http://127.0.0.1:${(second as WebServerApplicationContext).webServer!!.port}"
            val simultaneous = (1..12).map {
                client.sendAsync(request(url, "/api/v1/me", "GET", session), HttpResponse.BodyHandlers.ofString())
            }
            CompletableFuture.allOf(*simultaneous.toTypedArray()).join()
            simultaneous.forEach { assertEquals(200, it.join().statusCode(), it.join().body()) }
            val replay = send(url, enroll, "POST", session, csrf, key)
            assertEquals(201, replay.statusCode(), replay.body())
            assertEquals(first.body(), replay.body())
            assertEquals("true", replay.headers().firstValue("Idempotency-Replayed").orElseThrow())
            val id = app.mapper.readTree(first.body()).get("enrollmentId").asString()
            assertEquals(200, send(url, "/api/v1/enrollments/$id", "DELETE", session, csrf, UUID.randomUUID().toString()).statusCode())
            assertEquals(403, send(url, "/api/v1/auth/logout", "POST", session).statusCode())
            assertEquals(200, send(url, "/api/v1/auth/logout", "POST", session, csrf).statusCode())
            assertEquals(401, send(app.base, "/api/v1/me", cookie = session).statusCode())
        }
        assertEquals(0L, app.db.count("SELECT count(*) FROM identity.spring_session"))
    }

    @Test
    fun `timeout fails closed and old session is usable after connectivity recovers`() {
        val f = fixture()
        val (session, csrf) = login(app.base, f)
        val path = "/api/v1/sections/${f.section}/enrollments"
        val docker = redis.dockerClient
        docker.pauseContainerCmd(redis.containerId).exec()
        try {
            val failure = send(app.base, path, "POST", session, csrf, UUID.randomUUID().toString())
            assertEquals(503, failure.statusCode(), failure.body())
            assertFalse(failure.body().contains(password))
            assertEquals(0L, app.db.count("SELECT count(*) FROM enrollment.enrollments WHERE student_id=?", f.student))
        } finally {
            docker.unpauseContainerCmd(redis.containerId).exec()
        }
        assertEquals(200, send(app.base, "/api/v1/me", cookie = session).statusCode())
        app.db.connection().use { it.execute("UPDATE identity.test_accounts SET auth_version=auth_version+1 WHERE principal_id=?", f.principal) }
        assertEquals(401, send(app.base, "/api/v1/me", cookie = session).statusCode())
        assertEquals(401, send(app.base, "/api/v1/me", cookie = session).statusCode())
    }

    @Test
    fun `redis restart requires login and preserves an earlier durable outcome`() {
        val f = fixture()
        val (oldSession, oldCsrf) = login(app.base, f)
        val path = "/api/v1/sections/${f.section}/enrollments"
        val key = UUID.randomUUID().toString()
        val first = send(app.base, path, "POST", oldSession, oldCsrf, key)
        assertEquals(201, first.statusCode(), first.body())

        assertEquals(0, redis.execInContainer("sh", "-c", "kill -USR1 1").exitCode)
        val status = (1..12).asSequence().map {
            val response = send(app.base, "/api/v1/me", cookie = oldSession).statusCode()
            if (response != 401) Thread.sleep(250)
            response
        }.firstOrNull { it == 401 }
        assertEquals(401, status)
        val (session, csrf) = login(app.base, f)
        val replay = send(app.base, path, "POST", session, csrf, key)
        assertEquals(first.body(), replay.body())
        assertEquals("true", replay.headers().firstValue("Idempotency-Replayed").orElseThrow())
    }

    @Test
    fun `store changes require login and redis idle sessions expire`() {
        val f = fixture()
        val (redisCookie, _) = login(app.base, f)
        app.replica("--server.servlet.session.cookie.secure=true").use { jdbc ->
            val url = "http://127.0.0.1:${(jdbc as WebServerApplicationContext).webServer!!.port}"
            assertEquals(1, jdbc.getBeansOfType(JdbcIndexedSessionRepository::class.java).size)
            assertEquals(401, send(url, "/api/v1/me", cookie = redisCookie).statusCode())
            val (jdbcCookie, _) = login(url, f)
            assertEquals(401, send(app.base, "/api/v1/me", cookie = jdbcCookie).statusCode())
            app.db.connection().use { it.execute("DELETE FROM identity.spring_session") }
            assertEquals(401, send(url, "/api/v1/me", cookie = jdbcCookie).statusCode())
        }
        app.replica(*settings, "--server.servlet.session.cookie.secure=true", "--spring.session.timeout=2s").use { expiring ->
            val url = "http://127.0.0.1:${(expiring as WebServerApplicationContext).webServer!!.port}"
            val (cookie, _) = login(url, f)
            Thread.sleep(2200)
            assertEquals(401, send(url, "/api/v1/me", cookie = cookie).statusCode())
        }
    }

    private fun fixture(): Fixture = Fixture().populate(app.db).also { f ->
        app.db.connection().use { it.execute("UPDATE identity.test_accounts SET password_hash=? WHERE principal_id=?",
            "{bcrypt}" + BCryptPasswordEncoder(4).encode(password), f.principal) }
    }

    private fun login(base: String, fixture: Fixture): Pair<String, String> {
        val initial = send(base, "/api/v1/auth/csrf")
        assertEquals(200, initial.statusCode(), initial.body())
        val previous = cookie(initial)
        val authenticated = send(base, "/api/v1/auth/login", "POST", previous, token(initial),
            form = "username=test-${fixture.principal}&password=$password")
        assertEquals(200, authenticated.statusCode(), authenticated.body())
        val session = cookie(authenticated)
        assertNotEquals(previous, session)
        assertEquals(401, send(base, "/api/v1/me", cookie = previous).statusCode())
        val csrf = send(base, "/api/v1/auth/csrf", cookie = session)
        assertEquals(200, csrf.statusCode(), csrf.body())
        return session to token(csrf)
    }

    private fun token(response: HttpResponse<String>) = app.mapper.readTree(response.body()).get("token").asString()

    private fun cookie(response: HttpResponse<String>): String {
        val header = response.headers().allValues("Set-Cookie").single { it.startsWith("SESSION=") }
        for (flag in listOf("Secure", "HttpOnly", "SameSite=Lax")) assertTrue(header.contains("; $flag"), header)
        return header.substringBefore(';')
    }

    private fun send(base: String, path: String, method: String = "GET", cookie: String? = null, csrf: String? = null,
        key: String? = null, form: String? = null): HttpResponse<String> =
        client.send(request(base, path, method, cookie, csrf, key, form), HttpResponse.BodyHandlers.ofString())

    private fun request(base: String, path: String, method: String, cookie: String? = null, csrf: String? = null,
        key: String? = null, form: String? = null): HttpRequest =
        HttpRequest.newBuilder(URI.create(base + path)).timeout(Duration.ofSeconds(8)).apply {
            cookie?.let { header("Cookie", it) }
            csrf?.let { header("X-CSRF-TOKEN", it) }
            key?.let { header("Idempotency-Key", it) }
            if (form != null) header("Content-Type", "application/x-www-form-urlencoded")
        }.method(method, form?.let { HttpRequest.BodyPublishers.ofString(it) } ?: HttpRequest.BodyPublishers.noBody()).build()
}
