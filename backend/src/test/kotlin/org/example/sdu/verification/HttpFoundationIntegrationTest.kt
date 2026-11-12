package org.example.sdu.verification

import com.zaxxer.hikari.HikariDataSource
import org.example.sdu.registration.RegistrationApplication
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir
import org.springframework.boot.SpringApplication
import org.springframework.boot.web.server.context.WebServerApplicationContext
import org.springframework.context.ConfigurableApplicationContext
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Configuration
import org.springframework.core.annotation.Order
import org.springframework.security.config.annotation.web.builders.HttpSecurity
import org.springframework.security.web.SecurityFilterChain
import org.springframework.session.jdbc.JdbcIndexedSessionRepository
import org.springframework.session.Session
import org.springframework.session.SessionRepository
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.RestController
import jdk.jfr.consumer.RecordingFile
import java.net.URI
import java.net.http.HttpClient
import java.net.http.HttpRequest
import java.net.http.HttpResponse
import java.nio.file.Files
import java.nio.file.Path
import java.time.Duration
import java.util.UUID

class HttpFoundationIntegrationTest : PostgresTest() {
    @Test
    fun `backend uses runtime PostgreSQL connection and exposes only low detail health`() = application { context, base ->
        val dataSource=context.getBean(HikariDataSource::class.java)
        dataSource.connection.use { c -> assertEquals("portal_runtime",c.value("SELECT current_user")) }
        for(path in listOf("/actuator/health","/actuator/health/liveness","/actuator/health/readiness")) {
            val response=get(base+path)
            assertEquals(200,response.statusCode(),response.body());assertTrue(response.body().contains("UP"))
            assertFalse(response.body().contains("components"));assertFalse(response.body().contains("password"))
        }
        assertEquals(401,get(base+"/api/v1/me/enrollments").statusCode())
        assertEquals(401,get(base+"/actuator/env").statusCode())
        val post=HttpRequest.newBuilder(URI.create(base+"/api/v1/me/enrollments")).POST(HttpRequest.BodyPublishers.ofString("{}")).build()
        assertEquals(403,client.send(post,HttpResponse.BodyHandlers.ofString()).statusCode())
    }

    @Test
    fun `correlation IDs and safe problem responses survive framework boundaries`() = application { _,base ->
        val id=UUID.randomUUID().toString()
        val supplied=get(base+"/actuator/health",id)
        assertEquals(id,supplied.headers().firstValue("X-Correlation-ID").orElseThrow())
        val rejected=get(base+"/actuator/health","untrusted-non-uuid")
        assertDoesNotThrow { UUID.fromString(rejected.headers().firstValue("X-Correlation-ID").orElseThrow()) }
        assertNotEquals(id,get(base+"/actuator/health").headers().firstValue("X-Correlation-ID").orElseThrow())
        val failed=get(base+"/__test__/unexpected",id)
        assertEquals(500,failed.statusCode());assertTrue(failed.body().contains("INTERNAL_ERROR"));assertTrue(failed.body().contains(id))
        assertFalse(failed.body().contains("sensitive-synthetic-detail"));assertFalse(failed.body().contains("stackTrace"))
        val invalid=get(base+"/__test__/invalid")
        assertEquals(400,invalid.statusCode());assertTrue(invalid.body().contains("INVALID_REQUEST"))
        assertFalse(invalid.body().contains("sensitive-synthetic-detail"))
    }

    @Test
    fun `standard JDBC session adapter saves rotates and deletes against Flyway tables`() = application { context,_ ->
        @Suppress("UNCHECKED_CAST")
        val repository=context.getBean(JdbcIndexedSessionRepository::class.java) as SessionRepository<Session>
        val session=repository.createSession()
        session.setAttribute("synthetic-marker","only-test-data")
        repository.save(session)
        val oldId=session.id
        val primary=db.value("SELECT primary_id FROM identity.spring_session WHERE session_id=?",oldId)
        assertEquals("only-test-data",repository.findById(oldId)!!.getAttribute<String>("synthetic-marker"))
        session.changeSessionId();repository.save(session)
        assertNull(repository.findById(oldId))
        assertEquals(primary,db.value("SELECT primary_id FROM identity.spring_session WHERE session_id=?",session.id))
        assertEquals(1L,db.count("SELECT count(*) FROM identity.spring_session_attributes"))
        repository.deleteById(session.id)
        assertEquals(0L,db.count("SELECT count(*) FROM identity.spring_session"))
        assertEquals(0L,db.count("SELECT count(*) FROM identity.spring_session_attributes"))
    }

    @Test
    fun `readiness fails during database access loss while liveness remains healthy`() = application { context,base ->
        val pool=context.getBean(HikariDataSource::class.java)
        try {
            db.connection().use { it.execute("ALTER ROLE portal_runtime NOLOGIN") }
            pool.hikariPoolMXBean.softEvictConnections()
            assertEquals(503,get(base+"/actuator/health/readiness").statusCode())
            assertEquals(200,get(base+"/actuator/health/liveness").statusCode())
        } finally { db.connection().use { it.execute("ALTER ROLE portal_runtime LOGIN") } }
        val deadline=System.nanoTime()+Duration.ofSeconds(8).toNanos()
        var status=get(base+"/actuator/health/readiness").statusCode()
        while(status!=200&&System.nanoTime()<deadline) { Thread.sleep(50);status=get(base+"/actuator/health/readiness").statusCode() }
        assertEquals(200,status)
    }

    @Test
    fun `benchmark samples the real servlet execution mode and writes JVM evidence`(@TempDir directory: Path) {
        for (virtual in listOf(false, true)) {
            val evidence = directory.resolve(virtual.toString())
            application("--spring.profiles.active=test,benchmark", "--management.server.port=-1", "--portal.monitoring.enabled=false",
                "--spring.threads.virtual.enabled=$virtual", "--spring.main.keep-alive=true",
                "--portal.benchmark.jvm-evidence=true", "--portal.benchmark.jvm-evidence-directory=$evidence") { _, base ->
                assertEquals(virtual.toString(), get("$base/__test__/virtual-thread").body())
                assertEquals(401, get("$base/api/v1/me").statusCode())
                Files.createFile(evidence.resolve("finish"))
                val deadline = System.nanoTime() + Duration.ofSeconds(10).toNanos()
                while (!Files.exists(evidence.resolve("complete")) && System.nanoTime() < deadline) Thread.sleep(50)
                assertTrue(Files.size(evidence.resolve("benchmark.jfr")) > 0)
                assertTrue(Files.size(evidence.resolve("threads-0.txt")) > 0)
                val counts = Files.readAllLines(evidence.resolve("requests.tsv")).last().split('\t')
                assertEquals(listOf(if (virtual) "1" else "0", if (virtual) "0" else "1"), counts.takeLast(2))
            }
        }
    }

    @Test
    fun `benchmark captures a dump and JFR marker when both pools fill`(@TempDir directory: Path) {
        application("--spring.profiles.active=test,benchmark", "--management.server.port=-1", "--portal.monitoring.enabled=false",
            "--server.tomcat.threads.max=2", "--server.tomcat.threads.min-spare=1",
            "--spring.datasource.hikari.maximum-pool-size=1", "--spring.datasource.hikari.connection-timeout=5000",
            "--portal.benchmark.jvm-evidence=true", "--portal.benchmark.saturation-evidence=true",
            "--portal.benchmark.jvm-evidence-directory=$directory") { context, base ->
            val requests = context.getBean(HikariDataSource::class.java).connection.use {
                val request = HttpRequest.newBuilder(URI.create("$base/__test__/checkout")).GET().build()
                val queued = List(2) { client.sendAsync(request, HttpResponse.BodyHandlers.discarding()) }
                awaitFile(directory.resolve("saturation.tsv"))
                queued
            }
            requests.forEach { assertEquals(200, it.get(5, java.util.concurrent.TimeUnit.SECONDS).statusCode()) }
            Files.createFile(directory.resolve("finish"))
            awaitFile(directory.resolve("complete"))
            val event = RecordingFile.readAllEvents(directory.resolve("benchmark.jfr")).single { it.eventType.name == "sdu.PoolSaturation" }
            assertEquals(2, event.getInt("tomcatBusy"))
            assertEquals(1, event.getInt("hikariActive"))
            assertTrue(Files.size(directory.resolve("threads-saturation-0.txt")) > 0)
        }
    }

    private fun awaitFile(file: Path) {
        val deadline = System.nanoTime() + Duration.ofSeconds(4).toNanos()
        while (!Files.exists(file) && System.nanoTime() < deadline) Thread.sleep(25)
        assertTrue(Files.exists(file), "Missing ${file.fileName}")
    }

    private fun application(vararg overrides: String, block:(ConfigurableApplicationContext,String)->Unit) {
        val application=SpringApplication(RegistrationApplication::class.java,TestWebConfiguration::class.java)
        application.setDefaultProperties(mapOf(
            "DB_URL" to db.url,"DB_MIGRATION_USERNAME" to TestPostgres.container.username,
            "DB_MIGRATION_PASSWORD" to TestPostgres.container.password,"DB_RUNTIME_PASSWORD" to TestPostgres.runtimePassword
        ))
        val profile = overrides.firstOrNull { it.startsWith("--spring.profiles.active=") } ?: "--spring.profiles.active=test"
        application.run(profile,"--server.port=0",*overrides.filterNot { it == profile }.toTypedArray()).use { context ->
            val port=requireNotNull((context as WebServerApplicationContext).webServer).port
            block(context,"http://127.0.0.1:$port")
        }
    }
    private fun get(url:String,correlation:String?=null):HttpResponse<String> {
        val request=HttpRequest.newBuilder(URI.create(url)).timeout(Duration.ofSeconds(5))
        if(correlation!=null) request.header("X-Correlation-ID",correlation)
        return client.send(request.GET().build(),HttpResponse.BodyHandlers.ofString())
    }
    companion object { private val client=HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(2)).build() }
}

// Explicitly registered test-only routes live outside the production component-scan root.
@Configuration(proxyBeanMethods=false)
class TestWebConfiguration {
    @Bean fun probeController(dataSource: HikariDataSource) = ProbeController(dataSource)
    @Bean @Order(0)
    fun probeSecurity(http:HttpSecurity):SecurityFilterChain = http.securityMatcher("/__test__/**")
        .authorizeHttpRequests { it.anyRequest().permitAll() }.build()
}

@RestController
class ProbeController(private val dataSource: HikariDataSource) {
    @GetMapping("/__test__/virtual-thread") fun virtualThread(): String = Thread.currentThread().isVirtual.toString()
    @GetMapping("/__test__/checkout") fun checkout(): String = dataSource.connection.use { "OK" }
    @GetMapping("/__test__/unexpected") fun unexpected():String = error("sensitive-synthetic-detail")
    @GetMapping("/__test__/invalid") fun invalid():String = throw IllegalArgumentException("sensitive-synthetic-detail")
}
