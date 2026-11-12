package org.example.sdu.verification

import org.example.sdu.registration.RegistrationApplication
import org.example.sdu.registration.authentication.internal.AccountPrincipal
import org.example.sdu.registration.enrollment.api.*
import org.springframework.boot.SpringApplication
import org.springframework.boot.web.server.context.WebServerApplicationContext
import org.springframework.context.ConfigurableApplicationContext
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken
import org.springframework.security.core.context.SecurityContextHolder
import org.springframework.security.core.userdetails.UserDetailsService
import tools.jackson.databind.json.JsonMapper
import java.util.UUID

/** One real application and isolated migrated PostgreSQL database per test class. */
class Phase5Harness(
    vararg overrides: String,
    private val extraSource: Class<*>? = null,
    private val prepare: ((TestDatabase) -> Unit)? = null,
    private val runtimeDatabaseUrl: ((TestDatabase) -> String)? = null,
) : AutoCloseable {
    val db = TestDatabase.create()
    val context: ConfigurableApplicationContext = run { prepare?.invoke(db); replica(*overrides) }
    fun replica(vararg overrides: String): ConfigurableApplicationContext = SpringApplication(*listOfNotNull(RegistrationApplication::class.java, extraSource).toTypedArray()).also {
        it.setDefaultProperties(mapOf("DB_URL" to db.url, "DB_MIGRATION_USERNAME" to TestPostgres.container.username,
            "DB_MIGRATION_PASSWORD" to TestPostgres.container.password, "DB_RUNTIME_PASSWORD" to TestPostgres.runtimePassword))
    }.run("--spring.profiles.active=test", "--server.port=0", "--logging.level.root=WARN",
        *listOfNotNull(runtimeDatabaseUrl?.let { "--spring.datasource.url=${it(db)}" }).toTypedArray(), *overrides)
    val base = "http://127.0.0.1:${requireNotNull((context as WebServerApplicationContext).webServer).port}"
    val commands = context.getBean(EnrollmentCommands::class.java)
    val mapper = context.getBean(JsonMapper::class.java)
    fun <T> asStudent(fixture: Fixture, action: () -> T): T {
        val principal = context.getBean(UserDetailsService::class.java).loadUserByUsername("test-${fixture.principal}") as AccountPrincipal
        principal.eraseCredentials()
        val old = SecurityContextHolder.getContext()
        SecurityContextHolder.setContext(SecurityContextHolder.createEmptyContext().also {
            it.authentication = UsernamePasswordAuthenticationToken.authenticated(principal, null, principal.authorities)
        })
        return try { action() } finally { SecurityContextHolder.setContext(old) }
    }
    fun enroll(f: Fixture, section: UUID = f.section, key: UUID = UUID.randomUUID()) = asStudent(f) { commands.enroll(section, key.toString()) }
    fun withdraw(f: Fixture, id: UUID, key: UUID = UUID.randomUUID()) = asStudent(f) { commands.withdraw(id, key.toString()) }
    fun id(result: CommittedOutcome): UUID = UUID.fromString(mapper.readTree(result.body).get("enrollmentId").asString())
    override fun close() { try { context.close() } finally { db.close() } }
}

fun Fixture.anotherStudent(db: TestDatabase): Fixture {
    val next = copy(student = UUID.randomUUID(), principal = UUID.randomUUID(), snapshot = UUID.randomUUID())
    db.transaction { c ->
        c.execute("INSERT INTO students.students VALUES (?,?,'Synthetic Student',clock_timestamp())", next.student, "synthetic-${next.student}")
        c.execute("INSERT INTO identity.test_accounts VALUES (?,?,?,?,'STUDENT',true,1,clock_timestamp())", next.principal, next.student, "test-${next.principal}", TestPostgres.syntheticVerifier)
        c.execute("INSERT INTO students.eligibility_snapshots VALUES (?,?,'fixture-v1','COMPLETE','ACTIVE',clock_timestamp())", next.snapshot, next.student)
        c.execute("INSERT INTO enrollment.student_terms VALUES (?,?,?,'TEST',?,NULL,clock_timestamp())", next.student, term, policy, next.snapshot)
    }
    return next
}
fun Fixture.anotherOffering(db: TestDatabase, conflict: Boolean = false): Fixture {
    val next = copy(course = UUID.randomUUID(), offering = UUID.randomUUID(), section = UUID.randomUUID())
    db.transaction { c ->
        c.execute("INSERT INTO catalog.courses VALUES (?,?,'Synthetic Course',clock_timestamp())", next.course, "TEST${next.course.toString().take(8).uppercase()}")
        c.execute("INSERT INTO catalog.course_offerings VALUES (?,?,?,'Published Synthetic Course',3.00,'PUBLISHED',1,clock_timestamp())", next.offering, term, next.course)
        c.execute("INSERT INTO sections.course_sections (section_id,offering_id,term_id,section_code,capacity,publication_status,published_at,config_version) VALUES (?,?,?,'A',30,'PUBLISHED',clock_timestamp(),1)", next.section, next.offering, term)
        c.execute("INSERT INTO rules.offering_policy_eligibility VALUES (?,?,?)", next.offering, policy, term)
        c.execute("INSERT INTO sections.section_meetings SELECT ?,?,starts_at+CAST(? AS interval),ends_at+CAST(? AS interval),meeting_kind,is_required,venue FROM sections.section_meetings WHERE section_id=?", UUID.randomUUID(), next.section, if(conflict) "0 seconds" else "2 hours", if(conflict) "0 seconds" else "2 hours", section)
    }
    return next
}
