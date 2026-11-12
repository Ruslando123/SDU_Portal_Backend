package org.example.sdu.verification

import org.example.sdu.registration.bootstrap.database.RuntimeCredentialsCallback
import org.flywaydb.core.Flyway
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.BeforeEach
import org.testcontainers.postgresql.PostgreSQLContainer
import org.testcontainers.utility.DockerImageName
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder
import java.sql.Connection
import java.sql.DriverManager
import java.sql.SQLException
import java.time.Instant
import java.time.OffsetDateTime
import java.time.ZoneOffset
import java.util.UUID

object TestPostgres {
    const val IMAGE = "postgres:18.6-alpine@sha256:123b17998ccaf47e53b502e8c6a1e79afd8f579c6f3eec83c563da026f957395"
    val runtimePassword: String = UUID.randomUUID().toString() + UUID.randomUUID()
    val syntheticVerifier: String = "{bcrypt}" + BCryptPasswordEncoder(4).encode(UUID.randomUUID().toString())
    val container: PostgreSQLContainer by lazy {
        PostgreSQLContainer(DockerImageName.parse(IMAGE).asCompatibleSubstituteFor("postgres")).withDatabaseName("postgres")
            .withUsername("integration_admin").withPassword(UUID.randomUUID().toString()).also { container ->
                container.start()
                Runtime.getRuntime().addShutdownHook(Thread { container.stop() })
            }
    }
}

class TestDatabase private constructor(val name: String) : AutoCloseable {
    val url: String = "jdbc:postgresql://${TestPostgres.container.host}:${TestPostgres.container.getMappedPort(5432)}/$name"
    fun connection(runtime: Boolean = false): Connection = DriverManager.getConnection(
        url, if (runtime) "portal_runtime" else TestPostgres.container.username,
        if (runtime) TestPostgres.runtimePassword else TestPostgres.container.password
    )
    fun flyway(target: String? = null): Flyway = Flyway.configure()
        .also { if (target != null) it.target(target) }
        .dataSource(url, TestPostgres.container.username, TestPostgres.container.password)
        .defaultSchema("public").locations("classpath:db/migration")
        .cleanDisabled(true).validateMigrationNaming(true)
        .callbacks(RuntimeCredentialsCallback(TestPostgres.runtimePassword)).load()

    fun <T> transaction(runtime: Boolean = false, block: (Connection) -> T): T = connection(runtime).use { c ->
        c.autoCommit = false
        try { block(c).also { c.commit() } }
        catch (failure: Throwable) { c.rollback(); throw failure }
    }
    fun value(sql: String, vararg params: Any?): Any? = connection().use { it.value(sql, *params) }
    fun count(sql: String, vararg params: Any?): Long = (value(sql, *params) as Number).toLong()
    fun invariants() {
        assertEquals(0L, count("""
            SELECT count(*) FROM (
              SELECT s.section_id FROM sections.course_sections s
              LEFT JOIN enrollment.enrollments e ON e.section_id=s.section_id AND e.status='ACTIVE'
              GROUP BY s.section_id,s.capacity,s.enrolled_count
              HAVING s.enrolled_count<0 OR s.enrolled_count>s.capacity OR s.enrolled_count<>count(e.enrollment_id)
            ) invalid
        """), "Section counter and ACTIVE ledger must agree")
        assertEquals(0L, count("SELECT count(*) FROM (SELECT section_id,student_id FROM enrollment.enrollments WHERE status='ACTIVE' GROUP BY section_id,student_id HAVING count(*)>1) invalid"))
        assertEquals(0L, count("SELECT count(*) FROM (SELECT student_id,offering_id FROM enrollment.enrollments WHERE status='ACTIVE' GROUP BY student_id,offering_id HAVING count(*)>1) invalid"))
        assertEquals(0L, count("SELECT count(*) FROM operations.idempotency_records WHERE state<>'COMPLETED'"))
        assertEquals(0L, count("""
            SELECT count(*) FROM (
              SELECT st.student_id,st.term_id FROM enrollment.student_terms st
              JOIN rules.registration_policies p ON p.policy_id=st.policy_id
              LEFT JOIN enrollment.enrollments e ON e.student_id=st.student_id AND e.term_id=st.term_id AND e.status='ACTIVE'
              LEFT JOIN catalog.course_offerings o ON o.offering_id=e.offering_id
              GROUP BY st.student_id,st.term_id,st.credit_limit_override,p.credit_limit
              HAVING coalesce(sum(o.credits),0)>coalesce(st.credit_limit_override,p.credit_limit)
            ) invalid
        """))
    }
    override fun close() {
        DriverManager.getConnection(TestPostgres.container.jdbcUrl, TestPostgres.container.username, TestPostgres.container.password).use {
            it.createStatement().use { statement -> statement.execute("DROP DATABASE \"$name\" WITH (FORCE)") }
        }
    }
    companion object {
        fun create(target: String? = null): TestDatabase {
            val name = "verify_" + UUID.randomUUID().toString().replace("-", "")
            DriverManager.getConnection(TestPostgres.container.jdbcUrl, TestPostgres.container.username, TestPostgres.container.password).use {
                it.createStatement().use { statement -> statement.execute("CREATE DATABASE \"$name\"") }
            }
            return TestDatabase(name).also { it.flyway(target).migrate() }
        }
    }
}

abstract class PostgresTest {
    protected lateinit var db: TestDatabase
    @BeforeEach fun createDatabase() { db = TestDatabase.create() }
    @AfterEach fun verifyAndDropDatabase() { if (::db.isInitialized) try { db.invariants() } finally { db.close() } }
}

fun Connection.execute(sql: String, vararg params: Any?): Int = prepareStatement(sql).use { s ->
    params.forEachIndexed { index, p -> s.setObject(index + 1, if (p is Instant) OffsetDateTime.ofInstant(p, ZoneOffset.UTC) else p) }
    s.executeUpdate()
}
fun Connection.value(sql: String, vararg params: Any?): Any? = prepareStatement(sql).use { s ->
    params.forEachIndexed { index, p -> s.setObject(index + 1, if (p is Instant) OffsetDateTime.ofInstant(p, ZoneOffset.UTC) else p) }
    s.executeQuery().use { r -> check(r.next()); r.getObject(1) }
}
fun expectSqlState(state: String, action: () -> Unit): SQLException =
    assertThrows(SQLException::class.java) { action() }.also { assertEquals(state, it.sqlState) }

/** Synthetic SQL setup for database primitives and application fixtures; application effects use the owning services. */
data class Fixture(
    val term: UUID = UUID.randomUUID(), val student: UUID = UUID.randomUUID(), val principal: UUID = UUID.randomUUID(),
    val policy: UUID = UUID.randomUUID(), val snapshot: UUID = UUID.randomUUID(), val course: UUID = UUID.randomUUID(),
    val offering: UUID = UUID.randomUUID(), val section: UUID = UUID.randomUUID(),
    val enrollWindow: UUID = UUID.randomUUID(), val withdrawWindow: UUID = UUID.randomUUID()
) {
    fun populate(db: TestDatabase, capacity: Int = 30): Fixture {
        db.transaction { c ->
            c.execute("INSERT INTO students.students VALUES (?,?,'Synthetic Student',clock_timestamp())", student, "synthetic-$student")
            c.execute("INSERT INTO identity.test_accounts VALUES (?,?,?,?,'STUDENT',true,1,clock_timestamp())", principal, student, "test-$principal", TestPostgres.syntheticVerifier)
            c.execute("INSERT INTO catalog.academic_terms VALUES (?,?,current_date-1,current_date+180,'UTC')", term, term.toString().take(16))
            c.execute("INSERT INTO rules.term_policy_gates VALUES (?,'PREPARING',1,clock_timestamp())", term)
            c.execute("INSERT INTO rules.registration_policies VALUES (?,?,'TEST',1,18.00,clock_timestamp())", policy, term)
            c.execute("INSERT INTO rules.registration_windows VALUES (?,?,'ENROLL',clock_timestamp()-interval '1 hour',clock_timestamp()+interval '1 hour',false,NULL)", enrollWindow, policy)
            c.execute("INSERT INTO rules.registration_windows VALUES (?,?,'WITHDRAW',clock_timestamp()-interval '1 hour',clock_timestamp()+interval '1 hour',false,NULL)", withdrawWindow, policy)
            c.execute("INSERT INTO catalog.courses VALUES (?,?,'Synthetic Course',clock_timestamp())", course, "TEST${course.toString().take(8).uppercase()}")
            c.execute("INSERT INTO catalog.course_offerings VALUES (?,?,?,'Published Synthetic Course',3.00,'PUBLISHED',1,clock_timestamp())", offering, term, course)
            c.execute("INSERT INTO sections.course_sections (section_id,offering_id,term_id,section_code,capacity,publication_status,published_at,config_version) VALUES (?,?,?,'A',?,'PUBLISHED',clock_timestamp(),1)", section, offering, term, capacity)
            c.execute("INSERT INTO sections.section_meetings VALUES (?,?,clock_timestamp()+interval '2 days',clock_timestamp()+interval '2 days 1 hour','LECTURE',true,'Synthetic Room')", UUID.randomUUID(), section)
            c.execute("INSERT INTO students.eligibility_snapshots VALUES (?,?,'fixture-v1','COMPLETE','ACTIVE',clock_timestamp())", snapshot, student)
            c.execute("INSERT INTO rules.offering_policy_eligibility VALUES (?,?,?)", offering, policy, term)
            c.execute("INSERT INTO enrollment.student_terms VALUES (?,?,?,'TEST',?,NULL,clock_timestamp())", student, term, policy, snapshot)
            c.execute("UPDATE rules.term_policy_gates SET configuration_state='PUBLISHED',revision=2,updated_at=clock_timestamp() WHERE term_id=?", term)
        }
        return this
    }
    fun lock(c: Connection, sectionLock: Boolean = true) {
        c.value("SELECT term_id FROM rules.term_policy_gates WHERE term_id=? FOR SHARE", term)
        c.value("SELECT student_id FROM enrollment.student_terms WHERE student_id=? AND term_id=? FOR UPDATE", student, term)
        if (sectionLock) c.value("SELECT section_id FROM sections.course_sections WHERE section_id=? FOR UPDATE", section)
    }
    fun rawEpisode(c: Connection, id: UUID = UUID.randomUUID(), targetSection: UUID = section, targetOffering: UUID = offering, targetTerm: UUID = term): UUID {
        c.execute("INSERT INTO enrollment.enrollments VALUES (?,?,?,?,?,?,?,'ACTIVE',clock_timestamp(),NULL,NULL)", id, student, targetTerm, policy, targetOffering, targetSection, enrollWindow)
        return id
    }
    fun activeEpisode(db: TestDatabase): UUID = db.transaction { c ->
        lock(c)
        assertEquals(1, c.execute("UPDATE sections.course_sections SET enrolled_count=enrolled_count+1 WHERE section_id=? AND enrolled_count<capacity", section))
        rawEpisode(c)
    }
    fun claim(c: Connection, key: UUID = UUID.randomUUID(), operation: UUID = UUID.randomUUID(), collection: Boolean = false, historical: Boolean = false): UUID {
        c.execute("""
            INSERT INTO operations.idempotency_records
            (operation_id,principal_id,operation_type,operation_version,idempotency_key,request_fingerprint,target_type,target_id,state,created_at)
            VALUES (?,?,?,1,?,?,?,?,'CLAIMED',clock_timestamp()+CAST(? AS interval))
        """, operation, principal, if (collection) "ADMIN_CATALOG_CHANGE" else "ENROLL", key, ByteArray(32) { 7 }, if (collection) "COURSE_COLLECTION" else "SECTION", if (collection) null else section, if (historical) "-3 hours" else "0 seconds")
        return operation
    }
    fun complete(c: Connection, operation: UUID, expired: Boolean = false, successfulEpisode: UUID? = null, responseCode: String = if (successfulEpisode == null) "SECTION_FULL" else "ENROLLED") {
        c.execute("""
            UPDATE operations.idempotency_records SET state='COMPLETED',
              decided_at=clock_timestamp()+CAST(? AS interval),response_status=?,response_code=?,
              response_body=CAST(? AS jsonb),expires_at=clock_timestamp()+CAST(? AS interval)
            WHERE operation_id=?
        """, if (expired) "-2 hours" else "0 seconds", if (successfulEpisode == null) 409 else 201, responseCode, """{"code":"$responseCode","enrollmentId":${successfulEpisode?.let { "\"$it\"" } ?: "null"}}""", if (expired) "-1 hour" else "1 hour", operation)
    }
}
