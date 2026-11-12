package org.example.sdu.verification

import io.micrometer.core.instrument.MeterRegistry
import org.example.sdu.registration.enrollment.internal.EnrollmentAttempt
import org.example.sdu.registration.shared.errors.ApiFailure
import org.example.sdu.registration.students.api.StudentEvidence
import org.example.sdu.registration.students.api.AcademicEvidence
import org.junit.jupiter.api.*
import org.junit.jupiter.api.Assertions.*
import org.springframework.aop.support.AopUtils
import org.springframework.context.ConfigurableApplicationContext
import org.springframework.transaction.PlatformTransactionManager
import org.springframework.transaction.support.TransactionTemplate
import java.time.Duration
import java.util.UUID
import java.util.concurrent.Callable
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

@TestInstance(TestInstance.Lifecycle.PER_CLASS)
class EnrollmentImplementationTest {
    private lateinit var app: Phase5Harness
    private lateinit var f: Fixture
    @BeforeAll fun start() { app = Phase5Harness() }
    @AfterAll fun stop() { app.close() }
    @BeforeEach fun fixture() { f = Fixture().populate(app.db) }
    @AfterEach fun invariants() { Phase5InvariantChecks.verify(app.db) }

    @Test fun `successful enrollment commits episode audit and durable outcome through a real proxy`() {
        assertTrue(AopUtils.isAopProxy(app.context.getBean(EnrollmentAttempt::class.java)))
        val result=app.enroll(f);assertEquals(201,result.status);assertEquals("ENROLLED",result.code)
        assertEquals(1L,app.db.count("SELECT count(*) FROM enrollment.enrollments WHERE student_id=? AND status='ACTIVE'",f.student))
        assertEquals(1L,app.db.count("SELECT count(*) FROM audit.audit_events WHERE enrollment_id=? AND event_type='ENROLLED'",app.id(result)))
        assertEquals(1L,app.db.count("SELECT count(*) FROM operations.idempotency_records WHERE principal_id=? AND state='COMPLETED'",f.principal))
        assertEquals(1,app.context.getBeansOfType(org.springframework.transaction.PlatformTransactionManager::class.java).size)
        assertEquals(1L,app.db.count("""
            SELECT count(DISTINCT transaction_id) FROM (
                SELECT xmin::text AS transaction_id FROM sections.course_sections WHERE section_id=?
                UNION ALL SELECT xmin::text FROM enrollment.enrollments WHERE enrollment_id=?
                UNION ALL SELECT xmin::text FROM audit.audit_events WHERE enrollment_id=?
                UNION ALL SELECT xmin::text FROM operations.idempotency_records WHERE principal_id=?
            ) writes
        """,f.section,app.id(result),app.id(result),f.principal),"Seat, episode, audit and outcome share one PostgreSQL root transaction")
    }
    @Test fun `full section is a committed business denial`() {
        app.db.connection().use { it.execute("UPDATE sections.course_sections SET capacity=0 WHERE section_id=?",f.section) }
        val key=UUID.randomUUID();val result=app.enroll(f,key=key)
        assertEquals("SECTION_FULL",result.code);assertEquals(409,result.status)
        app.db.connection().use { it.execute("UPDATE sections.course_sections SET capacity=1 WHERE section_id=?",f.section) }
        assertEquals(result.body,app.enroll(f,key=key).body)
        assertEquals("ENROLLED",app.enroll(f).code)
    }
    @Test fun `different keys cannot duplicate a section or another section of the same offering`() {
        app.enroll(f);assertEquals("ALREADY_ENROLLED",app.enroll(f).code)
        val other=UUID.randomUUID()
        app.db.connection().use { it.execute("INSERT INTO sections.course_sections (section_id,offering_id,term_id,section_code,capacity,publication_status,published_at,config_version) VALUES (?,?,?,'B',30,'PUBLISHED',clock_timestamp(),1)",other,f.offering,f.term) }
        assertEquals("ALREADY_ENROLLED",app.enroll(f,other).code)
    }
    @Test fun `concurrent identical requests replay exactly the original terminal body`() {
        val key=UUID.randomUUID();val results=race({app.enroll(f,key=key)},{app.enroll(f,key=key)})
        assertEquals(1,results.count { it.replayed });assertEquals(results[0].body,results[1].body)
        assertEquals(1L,app.db.count("SELECT count(*) FROM audit.audit_events WHERE actor_principal_id=?",f.principal))
    }
    @Test fun `same scoped key with a different target is rejected without disclosing another result`() {
        val key=UUID.randomUUID();app.enroll(f,key=key)
        assertEquals("IDEMPOTENCY_KEY_REUSED",assertThrows(ApiFailure::class.java){app.enroll(f,UUID.randomUUID(),key)}.code)
        val other=f.anotherStudent(app.db);assertFalse(app.enroll(other,key=key).replayed)
    }
    @Test fun `exact credit arithmetic respects the pinned override`() {
        app.db.connection().use { it.execute("UPDATE enrollment.student_terms SET credit_limit_override=2.99 WHERE student_id=? AND term_id=?",f.student,f.term) }
        assertEquals("CREDIT_LIMIT_EXCEEDED",app.enroll(f).code)
    }
    @Test fun `required occurrences conflict but adjacent half open intervals do not`() {
        val other=f.anotherOffering(app.db,true);app.enroll(f)
        assertEquals("SCHEDULE_CONFLICT",app.enroll(f,other.section).code)
        app.db.connection().use { it.execute("UPDATE sections.section_meetings target SET starts_at=source.ends_at,ends_at=source.ends_at+interval '1 hour' FROM sections.section_meetings source WHERE target.section_id=? AND source.section_id=?",other.section,f.section) }
        assertEquals("ENROLLED",app.enroll(f,other.section).code)
    }
    @Test fun `prerequisites require passed completion and matching minimum grade scheme`() {
        val prerequisite=UUID.randomUUID()
        app.db.connection().use { c ->
            c.execute("INSERT INTO catalog.courses VALUES (?,?,'Synthetic Prerequisite',clock_timestamp())",prerequisite,"P${prerequisite.toString().take(8).uppercase()}")
            c.execute("INSERT INTO catalog.offering_prerequisites VALUES (?,?,70,'PERCENT')",f.offering,prerequisite)
        }
        assertEquals("PREREQUISITE_NOT_MET",app.enroll(f).code)
        app.db.connection().use { it.execute("INSERT INTO students.completed_courses VALUES (?,?,true,80,'OTHER',current_date)",f.snapshot,prerequisite) }
        assertEquals("PREREQUISITE_NOT_MET",app.enroll(f).code)
        app.db.connection().use { it.execute("UPDATE students.completed_courses SET grade_scheme='PERCENT' WHERE snapshot_id=?",f.snapshot) }
        assertEquals("ENROLLED",app.enroll(f).code)
    }

    @Test fun `joined academic snapshot keeps ownership and saves one JDBC query`() {
        fun read(context: ConfigurableApplicationContext, student: UUID = f.student): Pair<AcademicEvidence?, Long> =
            TransactionTemplate(context.getBean(PlatformTransactionManager::class.java)).execute {
                val registry = context.getBean(MeterRegistry::class.java)
                fun calls() = registry.find("database.sql").tag("operation", "select").timer()?.count() ?: 0L
                val before = calls()
                val evidence = context.getBean(StudentEvidence::class.java).pinned(student, f.snapshot)
                evidence to (calls() - before)
            }!!
        app.replica("--portal.performance.joined-academic-reads=false").use { control ->
            app.replica("--portal.performance.joined-academic-reads=true").use { joined ->
                assertEquals(read(control).first, read(joined).first)
                val completed = UUID.randomUUID()
                app.db.transaction { connection ->
                    connection.execute("INSERT INTO catalog.courses VALUES (?,?,'Synthetic Course',clock_timestamp())",
                        completed, "P${completed.toString().take(8).uppercase()}")
                    connection.execute("INSERT INTO students.completed_courses VALUES (?,?,true,80,'PERCENT',current_date)", f.snapshot, completed)
                }
                val baseline = read(control)
                assertEquals(2L, baseline.second)
                assertEquals(baseline.first to 1L, read(joined))
                assertTrue(read(joined, UUID.randomUUID()).first == null)
            }
        }
    }
    @Test fun `missing eligibility evidence fails unavailable and rolls back the claim`() {
        app.db.connection().use { it.execute("UPDATE students.eligibility_snapshots SET evidence_state='UNAVAILABLE' WHERE snapshot_id=?",f.snapshot) }
        assertEquals("ELIGIBILITY_DATA_UNAVAILABLE",assertThrows(ApiFailure::class.java){app.enroll(f)}.code)
        assertEquals(0L,app.db.count("SELECT count(*) FROM operations.idempotency_records WHERE principal_id=?",f.principal))
    }
    @Test fun `inactive evidence and missing offering allowlist are distinct denials`() {
        app.db.connection().use { it.execute("UPDATE students.eligibility_snapshots SET academic_status='INACTIVE' WHERE snapshot_id=?",f.snapshot) }
        assertEquals("ACADEMIC_INELIGIBLE",app.enroll(f).code)
        app.db.connection().use { c -> c.execute("UPDATE students.eligibility_snapshots SET academic_status='ACTIVE' WHERE snapshot_id=?",f.snapshot);c.execute("DELETE FROM rules.offering_policy_eligibility WHERE offering_id=?",f.offering) }
        val result=app.enroll(f);assertEquals(403,result.status);assertEquals("COHORT_INELIGIBLE",result.code)
    }
    @Test fun `closed registration window is a stable denial`() {
        closeWindow();assertEquals("REGISTRATION_CLOSED",app.enroll(f).code)
    }
    @Test fun `withdrawal releases once repeat preserves history and re enrollment uses a new episode`() {
        val key=UUID.randomUUID();val original=app.enroll(f,key=key);val id=app.id(original)
        val withdrawn=app.withdraw(f,id);assertEquals("WITHDRAWN",withdrawn.code)
        assertEquals("ALREADY_WITHDRAWN",app.withdraw(f,id).code)
        assertEquals(0,app.db.value("SELECT enrolled_count FROM sections.course_sections WHERE section_id=?",f.section))
        val next=app.enroll(f);assertNotEquals(id,app.id(next))
        assertEquals(original.body,app.enroll(f,key=key).body)
        assertEquals(2L,app.db.count("SELECT count(*) FROM enrollment.enrollments WHERE student_id=?",f.student))
    }
    @Test fun `withdrawal window is checked independently of enrollment window`() {
        val id=app.id(app.enroll(f))
        app.db.connection().use { it.execute("UPDATE rules.registration_windows SET is_closed=true,closed_at=clock_timestamp() WHERE window_id=?",f.withdrawWindow) }
        assertEquals("REGISTRATION_CLOSED",app.withdraw(f,id).code)
    }
    @Test fun `same student concurrent sections cannot jointly exceed credits`() {
        val other=f.anotherOffering(app.db)
        app.db.connection().use { it.execute("UPDATE enrollment.student_terms SET credit_limit_override=3.00 WHERE student_id=? AND term_id=?",f.student,f.term) }
        assertEquals(setOf("ENROLLED","CREDIT_LIMIT_EXCEEDED"),race({app.enroll(f)},{app.enroll(f,other.section)}).map { it.code }.toSet())
    }
    @Test fun `same student concurrent sections cannot jointly create a meeting conflict`() {
        val other=f.anotherOffering(app.db,true)
        assertEquals(setOf("ENROLLED","SCHEDULE_CONFLICT"),race({app.enroll(f)},{app.enroll(f,other.section)}).map { it.code }.toSet())
    }
    @Test fun `two students competing for the last seat allocate exactly once`() {
        val other=f.anotherStudent(app.db)
        app.db.connection().use { it.execute("UPDATE sections.course_sections SET capacity=1 WHERE section_id=?",f.section) }
        assertEquals(setOf("ENROLLED","SECTION_FULL"),race({app.enroll(f)},{app.enroll(other)}).map { it.code }.toSet())
    }
    @Test fun `failure after seat increment rolls back counter episode claim and audit`() {
        installFailure("enrollment.enrollments", "BEFORE INSERT", "INSERT", "23514")
        try { assertThrows(ApiFailure::class.java){app.enroll(f)};assertNoEffects() } finally { removeFailure("enrollment.enrollments") }
    }
    @Test fun `deferred failure at commit cannot return success and rolls back every write`() {
        installFailure("audit.audit_events", "AFTER INSERT", "AUDIT", "23514", deferred=true)
        try { assertThrows(ApiFailure::class.java){app.enroll(f)};assertNoEffects() } finally { removeFailure("audit.audit_events") }
    }
    @Test fun `lost response after acknowledged commit is recovered by the same key`() {
        val key=UUID.randomUUID();val lost=app.enroll(f,key=key)
        // Deliberately discard a committed response: the client makes no new intention.
        val recovered=app.enroll(f,key=key);assertTrue(recovered.replayed);assertEquals(lost.body,recovered.body)
    }
    @Test fun `expired scope is deleted by old operation UUID and reused atomically`() {
        val key=UUID.randomUUID();lateinit var old:UUID
        app.db.transaction { c -> old=f.claim(c,key,historical=true);f.complete(c,old,expired=true) }
        assertEquals("ENROLLED",app.enroll(f,key=key).code)
        assertEquals(0L,app.db.count("SELECT count(*) FROM operations.idempotency_records WHERE operation_id=?",old))
    }
    @Test fun `concurrent withdrawal and new enrollment preserve accounting`() {
        val original=app.id(app.enroll(f));val other=f.anotherStudent(app.db)
        val outcomes=race({app.withdraw(f,original)},{app.enroll(other)})
        assertEquals(setOf("WITHDRAWN","ENROLLED"),outcomes.map { it.code }.toSet())
    }
    @Test fun `controlled administrator close holds exclusive gate before enrollment proceeds`() {
        app.db.connection().use { admin ->
            admin.autoCommit=false;admin.value("SELECT term_id FROM rules.term_policy_gates WHERE term_id=? FOR UPDATE",f.term)
            val executor=Executors.newSingleThreadExecutor()
            try {
                val request=executor.submit(Callable { app.enroll(f) });awaitDatabaseWait("rules.term_policy_gates")
                admin.execute("UPDATE rules.registration_windows SET is_closed=true,closed_at=clock_timestamp() WHERE window_id=?",f.enrollWindow);admin.commit()
                assertEquals("REGISTRATION_CLOSED",request.get(8,TimeUnit.SECONDS).code)
            } finally { executor.shutdownNow();admin.rollback() }
        }
    }
    @Test fun `natural closing after section wait uses database wall time`() {
        app.db.connection().use { c ->
            c.execute("UPDATE rules.registration_windows SET closes_at=clock_timestamp()+interval '600 milliseconds' WHERE window_id=?",f.enrollWindow)
            c.autoCommit=false;c.value("SELECT section_id FROM sections.course_sections WHERE section_id=? FOR UPDATE",f.section)
            val executor=Executors.newSingleThreadExecutor()
            try {
                val request=executor.submit(Callable {app.enroll(f)});awaitDatabaseWait("sections.course_sections")
                Thread.sleep(700);c.commit();assertEquals("REGISTRATION_CLOSED",request.get(8,TimeUnit.SECONDS).code)
            } finally { executor.shutdownNow();c.rollback() }
        }
    }
    @Test fun `withdrawal keeps its enrollment when the window closes during the section wait`() {
        val id = app.id(app.enroll(f))
        app.db.connection().use { blocker ->
            blocker.execute("UPDATE rules.registration_windows SET closes_at=clock_timestamp()+interval '900 milliseconds' WHERE window_id=?", f.withdrawWindow)
            blocker.autoCommit = false
            blocker.value("SELECT section_id FROM sections.course_sections WHERE section_id=? FOR UPDATE", f.section)
            val executor = Executors.newSingleThreadExecutor()
            try {
                val request = executor.submit(Callable { app.withdraw(f, id) })
                awaitDatabaseWait("sections.course_sections")
                Thread.sleep(1000)
                blocker.commit()
                assertEquals("REGISTRATION_CLOSED", request.get(8, TimeUnit.SECONDS).code)
                assertEquals("ACTIVE", app.db.value("SELECT status FROM enrollment.enrollments WHERE enrollment_id=?", id))
            } finally {
                executor.shutdownNow()
                blocker.rollback()
            }
        }
    }
    @Test fun `foreign withdrawal is non enumerating and principal scoped replay grants no ownership`() {
        val original=app.id(app.enroll(f));val other=f.anotherStudent(app.db)
        assertEquals("RESOURCE_NOT_FOUND",app.withdraw(other,original).code)
        assertEquals("RESOURCE_NOT_FOUND",app.withdraw(other,UUID.randomUUID()).code)
        assertEquals("ACTIVE",app.db.value("SELECT status FROM enrollment.enrollments WHERE enrollment_id=?",original))
    }
    @Test fun `deadlock and serialization SQLSTATE retry fresh roots but exhaust finite attempts`() {
        for(state in listOf("40P01","40001")) {
            installRetryFailure(state,2)
            try { assertEquals("ENROLLED",app.enroll(f).code);assertEquals(3L,app.db.count("SELECT last_value FROM public.phase5_attempts")) }
            finally { removeFailure("enrollment.enrollments");app.db.connection().use { it.execute("DROP SEQUENCE public.phase5_attempts") } }
            f=Fixture().populate(app.db)
        }
        installRetryFailure("40P01",10)
        try {
            assertThrows(ApiFailure::class.java){app.enroll(f)}
            assertEquals(3L,app.db.count("SELECT last_value FROM public.phase5_attempts"));assertNoEffects()
        } finally { removeFailure("enrollment.enrollments");app.db.connection().use { it.execute("DROP SEQUENCE public.phase5_attempts") } }
    }
    @Test fun `section lock timeout is technical and leaves no claim or partial effects`() {
        app.db.connection().use { c ->
            c.autoCommit=false;c.value("SELECT section_id FROM sections.course_sections WHERE section_id=? FOR UPDATE",f.section)
            val started=System.nanoTime()
            try { assertEquals("TEMPORARILY_UNAVAILABLE",assertThrows(ApiFailure::class.java){app.enroll(f)}.code) }
            finally { c.rollback() }
            assertTrue(Duration.ofNanos(System.nanoTime()-started).seconds<7);assertNoEffects()
        }
    }
    @Test fun `bounded metrics record attempts replays denials locks and Hikari acquisition without identities`() {
        val key=UUID.randomUUID();app.enroll(f,key=key);app.enroll(f,key=key);app.enroll(f)
        val registry=app.context.getBean(MeterRegistry::class.java)
        for(name in listOf("enrollment.attempts","enrollment.replays","enrollment.denials","enrollment.transaction","enrollment.section.lock.wait","hikaricp.connections.acquire")) assertTrue(registry.find(name).meter()!=null,name)
        val prohibited=setOf("student_id","session_id","idempotency_key","principal_id")
        assertTrue(registry.meters.filter { it.id.name.startsWith("enrollment.") }.flatMap { it.id.tags }.none { it.key in prohibited || it.value.contains(f.student.toString()) })
    }

    @Test fun `different application instances coordinate one student through PostgreSQL`() {
        val other=f.anotherOffering(app.db)
        app.db.connection().use { it.execute("UPDATE enrollment.student_terms SET credit_limit_override=3.00 WHERE student_id=? AND term_id=?",f.student,f.term) }
        app.replica().use { replica ->
            val commands=replica.getBean(org.example.sdu.registration.enrollment.api.EnrollmentCommands::class.java)
            val results=race({app.enroll(f)}, {app.asStudent(f) {commands.enroll(other.section,UUID.randomUUID().toString())}})
            assertEquals(setOf("ENROLLED","CREDIT_LIMIT_EXCEEDED"),results.map {it.code}.toSet())
        }
    }
    @Test fun `pool exhaustion terminates within its bound before any root effect`() {
        val pool=app.context.getBean(com.zaxxer.hikari.HikariDataSource::class.java)
        app.asStudent(f) {
            val held=(1..3).map {pool.connection}
            try {
                val failure=assertThrows(ApiFailure::class.java) {app.commands.enroll(f.section,UUID.randomUUID().toString())}
                assertEquals(503,failure.status);assertNoEffects()
            } finally {held.forEach {it.close()}}
        }
    }
    @Test fun `same key wait expiration is request in progress and does not commit another claim`() {
        val key=UUID.randomUUID()
        app.db.connection().use { blocker ->
            blocker.autoCommit=false;f.claim(blocker,key)
            try {assertEquals("REQUEST_IN_PROGRESS",assertThrows(ApiFailure::class.java){app.enroll(f,key=key)}.code)}
            finally {blocker.rollback()}
        }
        assertNoEffects();assertEquals("ENROLLED",app.enroll(f,key=key).code)
    }

    private fun closeWindow() { app.db.connection().use { it.execute("UPDATE rules.registration_windows SET is_closed=true,closed_at=clock_timestamp() WHERE window_id=?",f.enrollWindow) } }
    private fun assertNoEffects() {
        assertEquals(0,app.db.value("SELECT enrolled_count FROM sections.course_sections WHERE section_id=?",f.section))
        assertEquals(0L,app.db.count("SELECT count(*) FROM enrollment.enrollments WHERE student_id=?",f.student))
        assertEquals(0L,app.db.count("SELECT count(*) FROM operations.idempotency_records WHERE principal_id=?",f.principal))
        assertEquals(0L,app.db.count("SELECT count(*) FROM audit.audit_events WHERE actor_principal_id=?",f.principal))
    }
    private fun installFailure(table:String,timing:String,marker:String,state:String,deferred:Boolean=false) {
        app.db.connection().use { c ->
            c.execute("CREATE FUNCTION public.phase5_fail() RETURNS trigger LANGUAGE plpgsql AS 'BEGIN RAISE EXCEPTION USING ERRCODE = ''$state'', MESSAGE = ''Synthetic $marker failure''; END'")
            c.execute("CREATE ${if(deferred) "CONSTRAINT " else ""}TRIGGER phase5_failure $timing ON $table ${if(deferred) "DEFERRABLE INITIALLY DEFERRED" else ""} FOR EACH ROW EXECUTE FUNCTION public.phase5_fail()")
        }
    }
    private fun installRetryFailure(state:String,failures:Int) {
        app.db.connection().use { c ->
            c.execute("CREATE SEQUENCE public.phase5_attempts")
            c.execute("GRANT USAGE,SELECT ON SEQUENCE public.phase5_attempts TO portal_runtime")
            c.execute("CREATE FUNCTION public.phase5_fail() RETURNS trigger LANGUAGE plpgsql AS 'BEGIN IF nextval(''public.phase5_attempts'') <= $failures THEN RAISE EXCEPTION USING ERRCODE = ''$state'', MESSAGE = ''Synthetic retry failure''; END IF; RETURN NEW; END'")
            c.execute("CREATE TRIGGER phase5_failure BEFORE INSERT ON enrollment.enrollments FOR EACH ROW EXECUTE FUNCTION public.phase5_fail()")
        }
    }
    private fun removeFailure(table:String) { app.db.connection().use { c -> c.execute("DROP TRIGGER phase5_failure ON $table");c.execute("DROP FUNCTION public.phase5_fail()") } }
    private fun awaitDatabaseWait(relation:String) {
        val deadline=System.nanoTime()+Duration.ofSeconds(4).toNanos()
        while(System.nanoTime()<deadline) {
            if(app.db.count("SELECT count(*) FROM pg_stat_activity WHERE datname=? AND usename='portal_runtime' AND wait_event_type='Lock' AND query LIKE ?",app.db.name,"%$relation%")>0) return
            Thread.sleep(10)
        }
        fail<Unit>("Expected a controlled PostgreSQL lock wait")
    }
    private fun <T> race(first:()->T,second:()->T):List<T> {
        val executor=Executors.newFixedThreadPool(2);val ready=CountDownLatch(2);val go=CountDownLatch(1)
        try {
            val futures=listOf(first,second).map { task -> executor.submit(Callable {ready.countDown();check(go.await(5,TimeUnit.SECONDS));task()}) }
            assertTrue(ready.await(5,TimeUnit.SECONDS));go.countDown();return futures.map { it.get(10,TimeUnit.SECONDS) }
        } finally { go.countDown();executor.shutdownNow() }
    }
}
