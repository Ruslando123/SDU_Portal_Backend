package org.example.sdu.verification

import com.zaxxer.hikari.HikariDataSource
import jakarta.servlet.DispatcherType
import jakarta.servlet.Filter
import jakarta.servlet.http.HttpServletRequest
import jakarta.servlet.http.HttpServletResponse
import org.example.sdu.registration.authentication.internal.AccountPrincipal
import org.junit.jupiter.api.*
import org.junit.jupiter.api.Assertions.*
import org.springframework.boot.web.servlet.FilterRegistrationBean
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Configuration
import org.springframework.core.MethodParameter
import org.springframework.core.Ordered
import org.springframework.http.MediaType
import org.springframework.jdbc.datasource.DataSourceUtils
import org.springframework.http.converter.HttpMessageConverter
import org.springframework.http.server.ServerHttpRequest
import org.springframework.http.server.ServerHttpResponse
import org.springframework.http.server.ServletServerHttpRequest
import org.springframework.security.core.context.SecurityContextHolder
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder
import org.springframework.transaction.support.TransactionOperations
import org.springframework.web.bind.annotation.ControllerAdvice
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.RestController
import org.springframework.web.servlet.HandlerInterceptor
import org.springframework.web.servlet.config.annotation.InterceptorRegistry
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer
import org.springframework.web.servlet.mvc.method.annotation.ResponseBodyAdvice
import java.io.IOException
import java.net.CookieManager
import java.net.CookiePolicy
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.net.Socket
import java.net.URI
import java.net.URLEncoder
import java.net.http.HttpClient
import java.net.http.HttpRequest
import java.net.http.HttpResponse
import java.net.http.HttpTimeoutException
import java.nio.charset.StandardCharsets.UTF_8
import java.time.Duration
import java.util.UUID
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicReference

@TestInstance(TestInstance.Lifecycle.PER_CLASS)
class SessionStorageReliabilityTest {
    private lateinit var link: DatabaseLink
    private lateinit var app: Phase5Harness
    private lateinit var fixture: Fixture
    private lateinit var password: String
    private lateinit var browser: Browser
    private val pool get() = app.context.getBean(HikariDataSource::class.java)
    private val faults get() = app.context.getBean(SessionFaults::class.java)
    private val enrollPath get() = "/api/v1/sections/${fixture.section}/enrollments"

    @BeforeAll
    fun start() {
        val postgres = TestPostgres.container
        link = DatabaseLink(InetSocketAddress(postgres.host, postgres.getMappedPort(5432)))
        app = Phase5Harness(
            "--server.servlet.session.cookie.secure=false",
            "--portal.session.autocommit-reads=true",
            "--spring.datasource.hikari.data-source-properties.socketTimeout=3",
            "--spring.datasource.hikari.data-source-properties.connectTimeout=1",
            extraSource = SessionFaultConfiguration::class.java,
            runtimeDatabaseUrl = { db -> "jdbc:postgresql://127.0.0.1:${link.port}/${db.name}?sslmode=disable" },
        )
    }

    @Test
    fun `session reads use autocommit and writes keep an independent transaction`() {
        val operations = app.context.getBean("springSessionTransactionOperations", TransactionOperations::class.java)
        fun assertMode(expected: Boolean) {
            val connection = DataSourceUtils.getConnection(pool)
            try { assertEquals(expected, connection.autoCommit) }
            finally { DataSourceUtils.releaseConnection(connection, pool) }
        }
        operations.execute { assertMode(true) }
        operations.executeWithoutResult { assertMode(false) }
    }

    @AfterAll
    fun stop() {
        try { if (::app.isInitialized) app.close() } finally { if (::link.isInitialized) link.close() }
    }

    @BeforeEach
    fun login() {
        fixture = Fixture().populate(app.db)
        password = UUID.randomUUID().toString()
        app.db.connection().use {
            it.execute("UPDATE identity.test_accounts SET password_hash=? WHERE principal_id=?",
                "{bcrypt}" + BCryptPasswordEncoder(4).encode(password), fixture.principal)
        }
        browser = Browser()
        browser.refreshCsrf()
        val form = "username=test-${fixture.principal}&password=${URLEncoder.encode(password, UTF_8)}"
        assertEquals(200, browser.send("POST", "/api/v1/auth/login", form = form).statusCode())
        browser.refreshCsrf()
    }

    @AfterEach
    fun verify() {
        faults.beforeController.set(null)
        faults.beforeBody.set(null)
        faults.afterFlush.set(null)
        restoreLink()
        // A flushed HTTP response can reach the client before the servlet thread finishes unwinding.
        val deadline = System.nanoTime() + Duration.ofSeconds(3).toNanos()
        while (pool.hikariPoolMXBean.activeConnections > 0 && System.nanoTime() < deadline) Thread.sleep(20)
        assertEquals(0, pool.hikariPoolMXBean.activeConnections)
        assertEquals(0, pool.hikariPoolMXBean.threadsAwaitingConnection)
        Phase5InvariantChecks.verify(app.db)
    }

    @Test
    fun `database connection loss before lookup fails closed and the original session recovers`() {
        val session = browser.cookie()
        val persisted = sessionRows()
        val dispatches = faults.errorDispatches.get()
        link.disconnect()
        try {
            assertUnavailable(browser.send("GET", "/api/v1/me"))
            assertUnavailable(Browser().send("GET", "/api/v1/auth/csrf"))
            assertEquals(dispatches, faults.errorDispatches.get())
            assertEquals(persisted, sessionRows())
            assertEquals(session, browser.cookie())
        } finally { restoreLink() }
        assertEquals(200, browser.send("GET", "/api/v1/me").statusCode())
        assertEquals(session, browser.cookie())
    }

    @Test
    fun `saturated pool returns a bounded safe 503 and releases all borrowers`() {
        val original = browser.cookie()
        val borrowed = List(pool.maximumPoolSize) { pool.connection }
        try {
            assertEquals(pool.maximumPoolSize, pool.hikariPoolMXBean.activeConnections)
            val started = System.nanoTime()
            assertUnavailable(browser.send("GET", "/api/v1/me"))
            assertTrue(Duration.ofNanos(System.nanoTime() - started) < Duration.ofSeconds(5))
            assertEquals(pool.maximumPoolSize, pool.hikariPoolMXBean.activeConnections)
            assertEquals(0, pool.hikariPoolMXBean.threadsAwaitingConnection)
        } finally { borrowed.forEach { it.close() } }
        assertEquals(200, browser.send("GET", "/api/v1/me").statusCode())
        assertEquals(original, browser.cookie())
    }

    @Test
    fun `connection loss after authentication leaves no enrollment and the same key can run`() {
        val key = UUID.randomUUID()
        faults.beforeController.set { link.disconnect() }
        try {
            assertUnavailable(browser.send("POST", enrollPath, key = key, fault = "before-controller"))
            org.junit.jupiter.api.Assertions.assertNull(faults.beforeController.get())
            assertEquals(0L, outcomeCount(key))
            assertEquals(0L, activeCount())
        } finally { restoreLink() }
        val recovered = browser.send("POST", enrollPath, key = key)
        assertEquals(201, recovered.statusCode())
        assertEquals("false", recovered.headers().firstValue("Idempotency-Replayed").orElseThrow())
    }

    @Test
    fun `session save failure after enrollment commit discards the buffered reply and replays the durable result`() {
        val key = UUID.randomUUID()
        val cookie = browser.cookie()
        val dispatches = faults.errorDispatches.get()
        faults.beforeBody.set {
            assertEquals(1L, outcomeCount(key))
            assertEquals(1L, activeCount())
            link.disconnect()
        }
        try {
            val failed = browser.send("POST", enrollPath, key = key, fault = "before-body")
            assertUnavailable(failed)
            assertFalse(failed.headers().firstValue("Idempotency-Replayed").isPresent)
            org.junit.jupiter.api.Assertions.assertNull(faults.beforeBody.get())
            assertEquals(dispatches, faults.errorDispatches.get())
        } finally { restoreLink() }
        val stored = app.db.value("SELECT response_body::text FROM operations.idempotency_records WHERE idempotency_key=?", key) as String
        val replay = browser.send("POST", enrollPath, key = key)
        assertEquals(201, replay.statusCode())
        assertEquals("true", replay.headers().firstValue("Idempotency-Replayed").orElseThrow())
        assertEquals(app.mapper.readTree(stored), app.mapper.readTree(replay.body()))
        assertEquals(1L, activeCount())
        assertEquals(1L, app.db.count("""
            SELECT count(*) FROM audit.audit_events a JOIN operations.idempotency_records o USING (operation_id)
            WHERE o.idempotency_key=? AND a.event_type='ENROLLED'
        """, key))
        assertEquals(cookie, browser.cookie())
    }

    @Test
    fun `timeout after commit replays the enrollment under the original key and permits withdrawal`() {
        val key = UUID.randomUUID()
        val committed = CountDownLatch(1)
        val release = CountDownLatch(1)
        faults.beforeBody.set {
            assertEquals(1L, outcomeCount(key))
            assertEquals(1L, activeCount())
            committed.countDown()
            check(release.await(6, TimeUnit.SECONDS))
        }
        try {
            assertThrows(HttpTimeoutException::class.java) {
                browser.send("POST", enrollPath, key = key, fault = "before-body", timeout = Duration.ofSeconds(2))
            }
            assertTrue(committed.await(1, TimeUnit.SECONDS))
        } finally {
            release.countDown()
        }
        val replay = browser.send("POST", enrollPath, key = key)
        assertEquals(201, replay.statusCode())
        assertEquals("true", replay.headers().firstValue("Idempotency-Replayed").orElseThrow())
        assertEquals(1L, outcomeCount(key))
        val episode = app.mapper.readTree(replay.body()).get("enrollmentId").asString()
        val withdrawn = browser.send("DELETE", "/api/v1/enrollments/$episode", key = UUID.randomUUID())
        assertEquals(200, withdrawn.statusCode())
        assertEquals(0L, activeCount())
        assertEquals("WITHDRAWN", app.db.value("SELECT status FROM enrollment.enrollments WHERE enrollment_id=?", UUID.fromString(episode)))
    }

    @Test
    fun `failure after HTTP commitment interrupts the exchange without an error dispatch into security`() {
        val cookie = browser.cookie()
        val dispatches = faults.errorDispatches.get()
        faults.afterFlush.set { link.disconnect() }
        try {
            val response = browser.stream("/api/v1/me/__test__/committed")
            assertEquals(200, response.statusCode())
            response.body().use {
                assertEquals("sent", String(it.readNBytes(4), UTF_8))
                assertThrows(IOException::class.java) { it.readAllBytes() }
            }
            assertEquals(dispatches, faults.errorDispatches.get())
            assertFalse(response.headers().firstValue("Retry-After").isPresent)
        } finally { restoreLink() }
        assertEquals(200, browser.send("GET", "/api/v1/me").statusCode())
        assertEquals(cookie, browser.cookie())
    }

    @Test
    fun `expired revoked and disabled sessions cannot authorize writes and CSRF failures remain 403`() {
        assertEquals(403, browser.send("POST", enrollPath, key = UUID.randomUUID(), csrf = "invalid").statusCode())
        assertEquals(403, browser.send("POST", enrollPath, key = UUID.randomUUID(), csrf = null).statusCode())
        app.db.connection().use {
            it.execute("UPDATE identity.spring_session SET expiry_time=0, last_access_time=0 WHERE principal_name=?", fixture.principal.toString())
        }
        assertEquals(401, browser.send("GET", "/api/v1/me").statusCode())
        loginAgain()
        app.db.connection().use {
            it.execute("UPDATE identity.test_accounts SET auth_version=auth_version+1 WHERE principal_id=?", fixture.principal)
        }
        assertEquals(401, browser.send("GET", "/api/v1/me").statusCode())
        loginAgain()
        app.db.connection().use {
            it.execute("UPDATE identity.test_accounts SET enabled=false WHERE principal_id=?", fixture.principal)
        }
        assertEquals(401, browser.send("GET", "/api/v1/me").statusCode())
        assertEquals(0L, activeCount())
    }

    @Test
    fun `known CSRF denial stays forbidden when the session refresh subsequently fails`() {
        app.db.connection().use { c ->
            c.execute("""
                CREATE FUNCTION public.phase7a_fail_refresh() RETURNS trigger LANGUAGE plpgsql AS
                $$ BEGIN RAISE EXCEPTION USING ERRCODE='57P01', MESSAGE='synthetic session refresh interruption'; END $$
            """)
            c.execute("CREATE TRIGGER phase7a_fail_refresh BEFORE UPDATE ON identity.spring_session FOR EACH ROW EXECUTE FUNCTION public.phase7a_fail_refresh()")
        }
        try {
            val response = browser.send("POST", enrollPath, key = UUID.randomUUID(), csrf = null)
            assertEquals(403, response.statusCode(), response.body())
            assertEquals("FORBIDDEN", app.mapper.readTree(response.body()).get("code").asString())
            assertFalse(response.headers().firstValue("Retry-After").isPresent)
            assertFalse(response.headers().firstValue("Set-Cookie").isPresent)
            assertEquals(0L, activeCount())
        } finally {
            app.db.connection().use { c ->
                c.execute("DROP TRIGGER phase7a_fail_refresh ON identity.spring_session")
                c.execute("DROP FUNCTION public.phase7a_fail_refresh()")
            }
        }
    }

    private fun loginAgain() {
        browser = Browser()
        browser.refreshCsrf()
        assertEquals(200, browser.send("POST", "/api/v1/auth/login", form = "username=test-${fixture.principal}&password=$password").statusCode())
        browser.refreshCsrf()
    }

    private fun activeCount() = app.db.count("SELECT count(*) FROM enrollment.enrollments WHERE student_id=? AND status='ACTIVE'", fixture.student)
    private fun outcomeCount(key: UUID) = app.db.count("SELECT count(*) FROM operations.idempotency_records WHERE idempotency_key=?", key)
    private fun sessionRows() = app.db.count("SELECT count(*) FROM identity.spring_session")

    private fun assertUnavailable(response: HttpResponse<String>) {
        assertEquals(503, response.statusCode(), response.body())
        assertEquals("1", response.headers().firstValue("Retry-After").orElseThrow())
        assertFalse(response.headers().firstValue("Set-Cookie").isPresent)
        val body = app.mapper.readTree(response.body())
        assertEquals("TEMPORARILY_UNAVAILABLE", body.get("code").asString())
        assertTrue(body.get("retryable").asBoolean())
        assertEquals("Session storage is temporarily unavailable.", body.get("detail").asString())
        assertEquals(response.headers().firstValue("X-Correlation-ID").orElseThrow(), body.get("correlationId").asString())
        assertFalse(response.body().contains("SQLException"))
        assertFalse(response.body().contains("identity.spring_session"))
    }

    private fun restoreLink() {
        if (link.reconnect()) pool.hikariPoolMXBean.softEvictConnections()
    }

    private inner class Browser {
        private val cookies = CookieManager(null, CookiePolicy.ACCEPT_ALL)
        private val client = HttpClient.newBuilder().version(HttpClient.Version.HTTP_1_1).cookieHandler(cookies).build()
        private var token: String? = null
        private var header = "X-CSRF-TOKEN"

        fun cookie() = cookies.cookieStore.cookies.single { it.name == "SESSION" }.value

        fun refreshCsrf() {
            val response = send("GET", "/api/v1/auth/csrf")
            assertEquals(200, response.statusCode(), response.body())
            val json = app.mapper.readTree(response.body())
            token = json.get("token").asString()
            header = json.get("headerName").asString()
        }

        fun send(method: String, path: String, key: UUID? = null, csrf: String? = token, form: String? = null,
                 fault: String? = null, timeout: Duration = Duration.ofSeconds(10)): HttpResponse<String> {
            val builder = HttpRequest.newBuilder(URI.create(app.base + path)).timeout(timeout)
            if (csrf != null) builder.header(header, csrf)
            if (key != null) builder.header("Idempotency-Key", key.toString())
            if (fault != null) builder.header("X-Session-Fault", fault)
            if (form != null) builder.header("Content-Type", "application/x-www-form-urlencoded")
            return client.send(builder.method(method, form?.let(HttpRequest.BodyPublishers::ofString)
                ?: HttpRequest.BodyPublishers.noBody()).build(), HttpResponse.BodyHandlers.ofString())
        }

        fun stream(path: String): HttpResponse<java.io.InputStream> = client.send(
            HttpRequest.newBuilder(URI.create(app.base + path)).timeout(Duration.ofSeconds(10)).build(),
            HttpResponse.BodyHandlers.ofInputStream(),
        )
    }
}

@Configuration(proxyBeanMethods = false)
class SessionFaultConfiguration : WebMvcConfigurer {
    private val switches = SessionFaults()

    @Bean fun sessionFaults() = switches
    @Bean fun sessionFaultAdvice() = SessionFaultAdvice(switches)
    @Bean fun committedResponseProbe() = CommittedResponseProbe(switches)

    override fun addInterceptors(registry: InterceptorRegistry) {
        registry.addInterceptor(object : HandlerInterceptor {
            override fun preHandle(request: HttpServletRequest, response: HttpServletResponse, handler: Any): Boolean {
                if (request.getHeader("X-Session-Fault") == "before-controller") {
                    check(SecurityContextHolder.getContext().authentication?.principal is AccountPrincipal)
                    switches.beforeController.getAndSet(null)?.invoke()
                }
                return true
            }
        })
    }

    @Bean
    fun countDownstreamErrorDispatch(): FilterRegistrationBean<Filter> = FilterRegistrationBean(
        Filter { request, response, chain ->
            switches.errorDispatches.incrementAndGet()
            chain.doFilter(request, response)
        }
    ).apply {
        order = Ordered.HIGHEST_PRECEDENCE + 2
        setDispatcherTypes(DispatcherType.ERROR)
    }
}

class SessionFaults {
    val beforeController = AtomicReference<(() -> Unit)?>(null)
    val beforeBody = AtomicReference<(() -> Unit)?>(null)
    val afterFlush = AtomicReference<(() -> Unit)?>(null)
    val errorDispatches = AtomicInteger()
}

@ControllerAdvice
class SessionFaultAdvice(private val faults: SessionFaults) : ResponseBodyAdvice<Any> {
    override fun supports(returnType: MethodParameter, converterType: Class<out HttpMessageConverter<*>>) = true

    override fun beforeBodyWrite(body: Any?, returnType: MethodParameter, selectedContentType: MediaType,
        selectedConverterType: Class<out HttpMessageConverter<*>>, request: ServerHttpRequest, response: ServerHttpResponse): Any? {
        if ((request as? ServletServerHttpRequest)?.servletRequest?.getHeader("X-Session-Fault") == "before-body") {
            faults.beforeBody.getAndSet(null)?.invoke()
        }
        return body
    }
}

@RestController
class CommittedResponseProbe(private val faults: SessionFaults) {
    @GetMapping("/api/v1/me/__test__/committed")
    fun stream(request: HttpServletRequest, response: HttpServletResponse) {
        val session = request.getSession(false) ?: error("Expected an authenticated session")
        response.setContentLength(128)
        response.outputStream.write("sent".toByteArray())
        response.flushBuffer()
        session.setAttribute("phase7a-test", "late change")
        faults.afterFlush.getAndSet(null)?.invoke()
    }
}

/** Isolates only application JDBC traffic. Operator queries still reach the real PostgreSQL container. */
private class DatabaseLink(private val target: InetSocketAddress) : AutoCloseable {
    private val loopback = InetAddress.getByName("127.0.0.1")
    private var listener: ServerSocket? = listen(0)
    private val connections = mutableSetOf<Pair<Socket, Socket>>()
    val port: Int = requireNotNull(listener).localPort

    init { accept(requireNotNull(listener)) }

    @Synchronized
    fun disconnect() {
        listener?.close()
        listener = null
        for ((client, server) in connections) {
            client.close()
            server.close()
        }
        connections.clear()
    }

    @Synchronized
    fun reconnect(): Boolean {
        if (listener != null) return false
        val server = listen(port)
        listener = server
        accept(server)
        return true
    }

    private fun listen(port: Int) = ServerSocket().apply {
        reuseAddress = true
        bind(InetSocketAddress(loopback, port))
    }

    private fun accept(server: ServerSocket) {
        Thread.startVirtualThread {
            while (!server.isClosed) {
                val client = try { server.accept() } catch (_: IOException) { break }
                connect(server, client)
            }
        }
    }

    @Synchronized
    private fun connect(server: ServerSocket, client: Socket) {
        if (listener !== server) {
            client.close()
            return
        }
        val upstream = Socket()
        try { upstream.connect(target, 1_000) } catch (_: IOException) {
            client.close()
            upstream.close()
            return
        }
        val pair = client to upstream
        connections.add(pair)
        relay(pair, client, upstream)
        relay(pair, upstream, client)
    }

    private fun relay(pair: Pair<Socket, Socket>, from: Socket, to: Socket) {
        Thread.startVirtualThread {
            try { from.getInputStream().transferTo(to.getOutputStream()) } catch (_: IOException) {
                // A cut closes both sides, including transfers currently blocked in read().
            } finally {
                synchronized(this) {
                    if (connections.remove(pair)) {
                        pair.first.close()
                        pair.second.close()
                    }
                }
            }
        }
    }

    override fun close() = disconnect()
}
