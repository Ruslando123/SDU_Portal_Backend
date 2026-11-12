package org.example.sdu.verification

import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import java.sql.Connection
import java.sql.SQLException
import java.util.UUID
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

class LockingIntegrationTest : PostgresTest() {
    @Test
    fun `shared policy gates are compatible but exclusive close must wait`() {
        val f = Fixture().populate(db)
        db.connection().use { a -> db.connection().use { b ->
            a.autoCommit=false; b.autoCommit=false
            a.value("SELECT term_id FROM rules.term_policy_gates WHERE term_id=? FOR SHARE", f.term)
            b.value("SELECT term_id FROM rules.term_policy_gates WHERE term_id=? FOR SHARE", f.term)
            b.rollback()
            b.execute("SET LOCAL lock_timeout='250ms'")
            expectSqlState("55P03") { b.value("SELECT term_id FROM rules.term_policy_gates WHERE term_id=? FOR UPDATE", f.term) }
            b.rollback(); a.commit()
            b.value("SELECT term_id FROM rules.term_policy_gates WHERE term_id=? FOR UPDATE", f.term)
            b.execute("UPDATE rules.registration_windows SET is_closed=true,closed_at=clock_timestamp() WHERE window_id=?", f.enrollWindow)
            b.execute("UPDATE rules.term_policy_gates SET revision=revision+1,updated_at=clock_timestamp() WHERE term_id=?", f.term)
            b.commit()
        } }
        assertEquals(true, db.value("SELECT is_closed FROM rules.registration_windows WHERE window_id=?", f.enrollWindow))
    }

    @Test
    fun `one student term mutex serializes requests independently of section locks`() {
        val f = Fixture().populate(db)
        db.connection().use { a -> db.connection().use { b ->
            a.autoCommit=false; b.autoCommit=false
            f.lock(a, sectionLock=false)
            b.value("SELECT term_id FROM rules.term_policy_gates WHERE term_id=? FOR SHARE", f.term)
            b.execute("SET LOCAL lock_timeout='250ms'")
            expectSqlState("55P03") { b.value("SELECT student_id FROM enrollment.student_terms WHERE student_id=? AND term_id=? FOR UPDATE", f.student, f.term) }
            b.rollback(); a.commit()
            assertEquals(f.student, b.value("SELECT student_id FROM enrollment.student_terms WHERE student_id=? AND term_id=? FOR UPDATE", f.student, f.term))
            b.commit()
        } }
    }

    @Test
    fun `idempotency claim does not take an early section FK lock`() {
        val f = Fixture().populate(db)
        db.connection().use { a -> db.connection().use { b ->
            a.autoCommit=false; b.autoCommit=false
            a.value("SELECT section_id FROM sections.course_sections WHERE section_id=? FOR UPDATE", f.section)
            b.execute("SET LOCAL statement_timeout='1s'")
            val op=f.claim(b); f.complete(b,op); b.commit(); a.rollback()
        } }
        assertEquals(1L, db.count("SELECT count(*) FROM operations.idempotency_records"))
    }

    @Test
    fun `an actual episode FK waits on referenced section protection`() {
        val f=Fixture().populate(db)
        db.connection().use { a -> db.connection().use { b ->
            a.autoCommit=false;b.autoCommit=false
            a.value("SELECT section_id FROM sections.course_sections WHERE section_id=? FOR UPDATE",f.section)
            b.execute("SET LOCAL lock_timeout='250ms'")
            expectSqlState("55P03") { f.rawEpisode(b) }
            b.rollback();a.rollback()
        } }
        assertEquals(0L,db.count("SELECT count(*) FROM enrollment.enrollments"))
    }

    @Test
    fun `conditional last seat update rechecks committed occupancy after waiting`() {
        val f=Fixture().populate(db,capacity=1);val other=anotherStudent(f)
        val worker=Executors.newSingleThreadExecutor()
        try { db.connection().use { a -> db.connection().use { b ->
            a.autoCommit=false;b.autoCommit=false
            val opA=f.claim(a);f.lock(a)
            assertEquals(1,a.execute("UPDATE sections.course_sections SET enrolled_count=enrolled_count+1 WHERE section_id=? AND enrolled_count<capacity",f.section))
            val episode=f.rawEpisode(a);f.complete(a,opA,successfulEpisode=episode)
            val pid=(b.value("SELECT pg_backend_pid()") as Number).toInt()
            val future=worker.submit<Int> {
                val op=other.claim(b);other.lock(b)
                val allocated=b.execute("UPDATE sections.course_sections SET enrolled_count=enrolled_count+1 WHERE section_id=? AND enrolled_count<capacity",f.section)
                if(allocated==1) other.rawEpisode(b)
                other.complete(b,op);b.commit();allocated
            }
            awaitBlocked(a,pid);a.commit()
            assertEquals(0,future.get(5,TimeUnit.SECONDS))
        } } } finally { worker.shutdownNow() }
        assertEquals(1L,db.count("SELECT enrolled_count FROM sections.course_sections WHERE section_id=?",f.section))
        assertEquals(1L,db.count("SELECT count(*) FROM enrollment.enrollments WHERE status='ACTIVE'"))
    }

    @Test
    fun `same key conflict waits and fresh statement sees the committed result`() {
        sameKeyRace(winnerCommits=true)
    }

    @Test
    fun `rolled back claim permits the waiting contender to execute`() {
        sameKeyRace(winnerCommits=false)
    }

    @Test
    fun `cleanup skips locked old result and cannot delete replacement UUID`() {
        val f=Fixture().populate(db);val key=UUID.randomUUID()
        val old=db.transaction { c -> f.claim(c,key,historical=true).also { f.complete(c,it,expired=true) } }
        db.connection().use { a -> db.connection().use { b ->
            a.autoCommit=false;b.autoCommit=false
            a.value("SELECT operation_id FROM operations.idempotency_records WHERE operation_id=? FOR UPDATE",old)
            assertEquals(0,(b.value("SELECT operations.cleanup_expired_outcomes(10)") as Number).toInt());b.commit()
            assertEquals(true,a.value("SELECT operations.delete_expired_outcome(?)",old))
            val replacement=f.claim(a,key);f.complete(a,replacement)
            assertEquals(0,(b.value("SELECT operations.cleanup_expired_outcomes(10)") as Number).toInt());b.commit()
            a.commit()
            assertEquals(0,(b.value("SELECT operations.cleanup_expired_outcomes(10)") as Number).toInt());b.commit()
            assertNotEquals(old,replacement)
        } }
        assertEquals(1L,db.count("SELECT count(*) FROM operations.idempotency_records"))
        assertEquals(0L,db.count("SELECT count(*) FROM operations.idempotency_records WHERE operation_id=?",old))
    }

    @Test
    fun `late section candidate reads academics without locking mutable capacity`() {
        val f=Fixture().populate(db)
        db.connection().use { a -> db.connection().use { b ->
            a.autoCommit=false;b.autoCommit=false
            val op=f.claim(a);f.lock(a,sectionLock=false)
            assertEquals("3.00",a.value("SELECT credits FROM catalog.course_offerings WHERE offering_id=?",f.offering).toString())
            assertEquals(1L,(a.value("SELECT count(*) FROM sections.section_meetings WHERE section_id=? AND is_required",f.section) as Number).toLong())
            b.value("SELECT term_id FROM rules.term_policy_gates WHERE term_id=? FOR SHARE",f.term)
            b.value("SELECT section_id FROM sections.course_sections WHERE section_id=? FOR UPDATE NOWAIT",f.section)
            b.execute("UPDATE sections.course_sections SET capacity=0,config_version=config_version+1 WHERE section_id=?",f.section);b.commit()
            a.value("SELECT section_id FROM sections.course_sections WHERE section_id=? FOR UPDATE",f.section)
            assertEquals(0,a.execute("UPDATE sections.course_sections SET enrolled_count=enrolled_count+1 WHERE section_id=? AND enrolled_count<capacity",f.section))
            f.complete(a,op);a.commit()
        } }
        assertEquals(0L,db.count("SELECT count(*) FROM enrollment.enrollments"))
    }

    @Test
    fun `late section academic reads see preceding same student credits and meetings`() {
        val f=Fixture().populate(db)
        val next=f.copy(course=UUID.randomUUID(),offering=UUID.randomUUID(),section=UUID.randomUUID())
        db.transaction { c ->
            c.execute("UPDATE rules.registration_policies SET credit_limit=3.00 WHERE policy_id=?",f.policy)
            c.execute("INSERT INTO catalog.courses VALUES (?,?,'Second Synthetic Course',clock_timestamp())",next.course,"TEST${next.course.toString().take(8).uppercase()}")
            c.execute("INSERT INTO catalog.course_offerings VALUES (?,?,?,'Second Published Course',3.00,'PUBLISHED',1,clock_timestamp())",next.offering,f.term,next.course)
            c.execute("INSERT INTO sections.course_sections VALUES (?,?,?,'A',30,0,'PUBLISHED',clock_timestamp(),1)",next.section,next.offering,f.term)
            c.execute("INSERT INTO sections.section_meetings VALUES (?,?,clock_timestamp()+interval '2 days',clock_timestamp()+interval '2 days 1 hour','LECTURE',true,'Synthetic Room')",UUID.randomUUID(),next.section)
            c.execute("INSERT INTO rules.offering_policy_eligibility VALUES (?,?,?)",next.offering,f.policy,f.term)
        }
        val worker=Executors.newSingleThreadExecutor()
        try { db.connection().use { a -> db.connection().use { b ->
            a.autoCommit=false;b.autoCommit=false
            val first=f.claim(a);f.lock(a)
            a.execute("UPDATE sections.course_sections SET enrolled_count=enrolled_count+1 WHERE section_id=? AND enrolled_count<capacity",f.section)
            val episode=f.rawEpisode(a);f.complete(a,first,successfulEpisode=episode)
            val pid=(b.value("SELECT pg_backend_pid()") as Number).toInt()
            val future=worker.submit<Pair<String,Boolean>> {
                val op=next.claim(b);next.lock(b,sectionLock=false)
                val credits=b.value("SELECT coalesce(sum(o.credits),0) FROM enrollment.enrollments e JOIN catalog.course_offerings o USING(offering_id) WHERE e.student_id=? AND e.term_id=? AND e.status='ACTIVE'",f.student,f.term).toString()
                val clash=b.value("""
                    SELECT EXISTS(SELECT 1 FROM enrollment.enrollments e
                    JOIN sections.section_meetings own ON own.section_id=e.section_id
                    JOIN sections.section_meetings target ON target.section_id=?
                    WHERE e.student_id=? AND e.term_id=? AND e.status='ACTIVE'
                      AND own.is_required AND target.is_required
                      AND own.starts_at<target.ends_at AND target.starts_at<own.ends_at)
                """,next.section,f.student,f.term) as Boolean
                b.value("SELECT section_id FROM sections.course_sections WHERE section_id=? FOR UPDATE",next.section)
                next.complete(b,op,responseCode="CREDIT_LIMIT_EXCEEDED");b.commit();credits to clash
            }
            awaitBlocked(a,pid);a.commit()
            assertEquals("3.00" to true,future.get(5,TimeUnit.SECONDS))
        } } } finally { worker.shutdownNow() }
        assertEquals(1L,db.count("SELECT count(*) FROM enrollment.enrollments WHERE status='ACTIVE'"))
    }

    @Test
    fun `post section wait wall clock catches natural close missed by transaction now`() {
        val f=Fixture().populate(db)
        db.transaction { it.execute("UPDATE rules.registration_windows SET closes_at=clock_timestamp()+interval '1200 milliseconds' WHERE window_id=?",f.enrollWindow) }
        val worker=Executors.newSingleThreadExecutor()
        try { db.connection().use { a -> db.connection().use { b ->
            a.autoCommit=false;b.autoCommit=false
            val op=f.claim(a);f.lock(a,sectionLock=false)
            b.value("SELECT term_id FROM rules.term_policy_gates WHERE term_id=? FOR SHARE",f.term)
            b.value("SELECT section_id FROM sections.course_sections WHERE section_id=? FOR UPDATE",f.section)
            val pid=(a.value("SELECT pg_backend_pid()") as Number).toInt()
            val future=worker.submit<Pair<Boolean,Boolean>> {
                a.value("SELECT section_id FROM sections.course_sections WHERE section_id=? FOR UPDATE",f.section)
                val stale=a.value("SELECT now()<closes_at FROM rules.registration_windows WHERE window_id=?",f.enrollWindow) as Boolean
                val fresh=a.value("SELECT clock_timestamp()<closes_at FROM rules.registration_windows WHERE window_id=?",f.enrollWindow) as Boolean
                f.complete(a,op,responseCode="REGISTRATION_CLOSED");a.commit();stale to fresh
            }
            awaitBlocked(b,pid)
            awaitCondition { b.value("SELECT clock_timestamp()>=closes_at FROM rules.registration_windows WHERE window_id=?",f.enrollWindow) as Boolean }
            b.commit();assertEquals(true to false,future.get(5,TimeUnit.SECONDS))
        } } } finally { worker.shutdownNow() }
    }

    @Test
    fun `deliberately unsafe shared gate upgrades produce a recoverable deadlock`() {
        val f=Fixture().populate(db);val workers=Executors.newFixedThreadPool(2)
        try { db.connection().use { a -> db.connection().use { b ->
            listOf(a,b).forEach { it.autoCommit=false;it.execute("SET LOCAL deadlock_timeout='100ms'");it.execute("SET LOCAL lock_timeout='3s'");it.value("SELECT term_id FROM rules.term_policy_gates WHERE term_id=? FOR SHARE",f.term) }
            val start=CountDownLatch(1)
            val futures=listOf(a,b).map { c -> workers.submit<String> {
                start.await()
                try { c.value("SELECT term_id FROM rules.term_policy_gates WHERE term_id=? FOR UPDATE",f.term);c.commit();"COMMITTED" }
                catch(e:SQLException) { c.rollback();e.sqlState }
            } }
            start.countDown()
            assertEquals(setOf("40P01","COMMITTED"),futures.map { it.get(5,TimeUnit.SECONDS) }.toSet())
        } } } finally { workers.shutdownNow() }
    }

    private fun sameKeyRace(winnerCommits:Boolean) {
        val f=Fixture().populate(db);val key=UUID.randomUUID();val opB=UUID.randomUUID();val worker=Executors.newSingleThreadExecutor()
        try { db.connection().use { a -> db.connection().use { b ->
            a.autoCommit=false;b.autoCommit=false
            val opA=f.claim(a,key);val pid=(b.value("SELECT pg_backend_pid()") as Number).toInt()
            val future=worker.submit<Int> {
                val changed=b.execute("""
                    INSERT INTO operations.idempotency_records
                    (operation_id,principal_id,operation_type,operation_version,idempotency_key,request_fingerprint,target_type,target_id,state,created_at)
                    VALUES (?,?,'ENROLL',1,?,?,'SECTION',?,'CLAIMED',clock_timestamp())
                    ON CONFLICT (principal_id,operation_type,operation_version,idempotency_key) DO NOTHING
                """,opB,f.principal,key,ByteArray(32){7},f.section)
                if(changed==1) f.complete(b,opB)
                else assertEquals("SECTION_FULL",b.value("SELECT response_code FROM operations.idempotency_records WHERE principal_id=? AND idempotency_key=?",f.principal,key))
                b.commit();changed
            }
            awaitBlocked(a,pid)
            if(winnerCommits) { f.complete(a,opA);a.commit() } else a.rollback()
            assertEquals(if(winnerCommits)0 else 1,future.get(5,TimeUnit.SECONDS))
        } } } finally { worker.shutdownNow() }
        assertEquals(1L,db.count("SELECT count(*) FROM operations.idempotency_records"))
    }

    private fun anotherStudent(f:Fixture):Fixture {
        val other=f.copy(student=UUID.randomUUID(),principal=UUID.randomUUID(),snapshot=UUID.randomUUID())
        db.transaction { c ->
            c.value("SELECT term_id FROM rules.term_policy_gates WHERE term_id=? FOR UPDATE",f.term)
            c.execute("INSERT INTO students.students VALUES (?,?,'Second Synthetic Student',clock_timestamp())",other.student,"synthetic-${other.student}")
            c.execute("INSERT INTO identity.test_accounts VALUES (?,?,?,?, 'STUDENT',true,1,clock_timestamp())",other.principal,other.student,"test-${other.principal}",TestPostgres.syntheticVerifier)
            c.execute("INSERT INTO students.eligibility_snapshots VALUES (?,?,'fixture-v1','COMPLETE','ACTIVE',clock_timestamp())",other.snapshot,other.student)
            c.execute("INSERT INTO enrollment.student_terms VALUES (?,?,?,'TEST',?,NULL,clock_timestamp())",other.student,f.term,f.policy,other.snapshot)
        };return other
    }

    private fun awaitBlocked(observer:Connection,pid:Int) = awaitCondition {
        observer.value("SELECT cardinality(pg_blocking_pids(?))>0",pid) as Boolean
    }
    private fun awaitCondition(condition:()->Boolean) {
        val deadline=System.nanoTime()+TimeUnit.SECONDS.toNanos(4)
        while(!condition()) { check(System.nanoTime()<deadline){"Database condition did not become observable"};Thread.sleep(20) }
    }
}
