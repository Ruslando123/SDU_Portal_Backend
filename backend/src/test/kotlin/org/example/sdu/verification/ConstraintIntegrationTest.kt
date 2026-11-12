package org.example.sdu.verification

import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import java.util.UUID

class ConstraintIntegrationTest : PostgresTest() {
    @Test
    fun `capacity and occupancy checks reject invalid updates without drift`() {
        val f = Fixture().populate(db)
        for (sql in listOf("SET capacity=-1", "SET enrolled_count=-1", "SET enrolled_count=31")) {
            expectSqlState("23514") { db.transaction { it.execute("UPDATE sections.course_sections $sql WHERE section_id=?", f.section) } }
            db.invariants()
        }
        f.activeEpisode(db)
        expectSqlState("23514") { db.transaction { it.execute("UPDATE sections.course_sections SET capacity=0 WHERE section_id=?", f.section) } }
        assertEquals(30L, db.count("SELECT capacity FROM sections.course_sections WHERE section_id=?", f.section))
    }

    @Test
    fun `both active partial unique indexes reject duplicates`() {
        val f = Fixture().populate(db); f.activeEpisode(db)
        expectSqlState("23505") { db.transaction { f.rawEpisode(it) } }
        val second = UUID.randomUUID()
        db.transaction { it.execute("INSERT INTO sections.course_sections VALUES (?,?,?,'B',30,0,'PUBLISHED',clock_timestamp(),1)", second, f.offering, f.term) }
        expectSqlState("23505") { db.transaction { f.rawEpisode(it, targetSection = second) } }
        assertEquals(1L, db.count("SELECT count(*) FROM enrollment.enrollments"))
    }

    @Test
    fun `withdrawal releases once and re enrollment preserves the old episode`() {
        val f = Fixture().populate(db); val old = f.activeEpisode(db)
        assertEquals(1, withdrawFixture(f, old))
        assertEquals(0, withdrawFixture(f, old))
        val fresh = f.activeEpisode(db)
        assertNotEquals(old, fresh)
        assertEquals("WITHDRAWN", db.value("SELECT status FROM enrollment.enrollments WHERE enrollment_id=?", old))
        assertEquals("ACTIVE", db.value("SELECT status FROM enrollment.enrollments WHERE enrollment_id=?", fresh))
        assertEquals(2L, db.count("SELECT count(*) FROM enrollment.enrollments"))
    }

    @Test
    fun `episode guard rejects identity mutation and withdrawn resurrection`() {
        val f = Fixture().populate(db); val episode = f.activeEpisode(db)
        expectSqlState("23514") { db.transaction { it.execute("UPDATE enrollment.enrollments SET enrolled_at=enrolled_at+interval '1 hour' WHERE enrollment_id=?", episode) } }
        withdrawFixture(f, episode)
        expectSqlState("23514") { db.transaction { it.execute("UPDATE enrollment.enrollments SET status='ACTIVE',withdrawn_at=NULL,withdraw_window_id=NULL WHERE enrollment_id=?", episode) } }
        assertEquals("WITHDRAWN", db.value("SELECT status FROM enrollment.enrollments WHERE enrollment_id=?", episode))
    }

    @Test
    fun `composite section offering term relationships cannot be mixed`() {
        val a = Fixture().populate(db); val b = Fixture().populate(db)
        expectSqlState("23503") { db.transaction { a.rawEpisode(it, targetSection = b.section) } }
        expectSqlState("23503") { db.transaction { a.rawEpisode(it, targetOffering = b.offering) } }
        expectSqlState("23503") { db.transaction { a.rawEpisode(it, targetTerm = b.term) } }
        assertEquals(0L, db.count("SELECT count(*) FROM enrollment.enrollments"))
    }

    @Test
    fun `pinned student evidence and policy cohort must agree`() {
        val f = Fixture().populate(db)
        val student = UUID.randomUUID()
        db.transaction { it.execute("INSERT INTO students.students VALUES (?,?,'Another Synthetic Student',clock_timestamp())", student, "synthetic-$student") }
        expectSqlState("23503") {
            db.transaction { it.execute("INSERT INTO enrollment.student_terms VALUES (?,?,?,'WRONG',?,NULL,clock_timestamp())", student, f.term, f.policy, f.snapshot) }
        }
        expectSqlState("23503") {
            db.transaction { it.execute("INSERT INTO enrollment.student_terms VALUES (?,?,?,'TEST',?,NULL,clock_timestamp())", student, f.term, f.policy, f.snapshot) }
        }
        assertEquals(1L, db.count("SELECT count(*) FROM enrollment.student_terms"))
    }

    @Test
    fun `nullable withdrawal composite FK is supplemented by state shape checks`() {
        val f = Fixture().populate(db); val episode = f.activeEpisode(db)
        assertNull(db.value("SELECT withdraw_window_id FROM enrollment.enrollments WHERE enrollment_id=?", episode))
        expectSqlState("23514") {
            db.transaction { it.execute("UPDATE enrollment.enrollments SET status='WITHDRAWN',withdrawn_at=clock_timestamp() WHERE enrollment_id=?", episode) }
        }
        val other = Fixture().populate(db)
        expectSqlState("23503") {
            db.transaction { it.execute("UPDATE enrollment.enrollments SET status='WITHDRAWN',withdrawn_at=clock_timestamp(),withdraw_window_id=? WHERE enrollment_id=?", other.withdrawWindow, episode) }
        }
        assertEquals("ACTIVE", db.value("SELECT status FROM enrollment.enrollments WHERE enrollment_id=?", episode))
    }

    @Test
    fun `NaN academic values are rejected by the F4-01 correction`() {
        val f = Fixture().populate(db)
        val cases = listOf(
            "UPDATE rules.registration_policies SET credit_limit='NaN' WHERE policy_id='${f.policy}'",
            "UPDATE catalog.course_offerings SET credits='NaN' WHERE offering_id='${f.offering}'",
            "UPDATE enrollment.student_terms SET credit_limit_override='NaN' WHERE student_id='${f.student}'",
            "INSERT INTO catalog.offering_prerequisites VALUES ('${f.offering}','${f.course}','NaN','TEST')",
            "INSERT INTO students.completed_courses VALUES ('${f.snapshot}','${f.course}',true,'NaN','TEST',current_date)"
        )
        cases.forEach { sql -> expectSqlState("23514") { db.transaction { it.execute(sql) } }; db.invariants() }
    }

    @Test
    fun `missing student mapping is rejected but multiple synthetic admins allow NULL`() {
        expectSqlState("23514") { db.transaction { it.execute("INSERT INTO identity.test_accounts VALUES (?,NULL,?,'encoded-fixture','STUDENT',true,1,clock_timestamp())", UUID.randomUUID(), "bad-${UUID.randomUUID()}") } }
        db.transaction { c -> repeat(2) { c.execute("INSERT INTO identity.test_accounts VALUES (?,NULL,?,?,'ADMIN',true,1,clock_timestamp())", UUID.randomUUID(), "admin-${UUID.randomUUID()}", TestPostgres.syntheticVerifier) } }
        assertEquals(2L, db.count("SELECT count(*) FROM identity.test_accounts WHERE student_id IS NULL"))
    }

    @Test
    fun `runtime role has no DDL audit editing or unrestricted outcome deletion`() {
        db.connection(runtime = true).use { c ->
            expectSqlState("42501") { c.execute("CREATE TABLE catalog.unauthorized(id integer)") }
            expectSqlState("42501") { c.execute("DELETE FROM audit.audit_events") }
            expectSqlState("42501") { c.execute("UPDATE audit.audit_events SET details='{}'") }
            expectSqlState("42501") { c.execute("DELETE FROM operations.idempotency_records") }
            assertEquals(false, c.value("SELECT rolsuper OR rolcreatedb OR rolcreaterole OR rolbypassrls FROM pg_roles WHERE rolname=current_user"))
        }
        db.connection().use { c ->
            c.execute("SET ROLE portal_observer")
            assertEquals(0L, (c.value("SELECT count(*) FROM sections.occupancy_summary") as Number).toLong())
            expectSqlState("42501") { c.value("SELECT count(*) FROM identity.test_accounts") }
        }
    }

    private fun withdrawFixture(f: Fixture, episode: UUID): Int = db.transaction { c ->
        f.lock(c)
        val changed = c.execute("UPDATE enrollment.enrollments SET status='WITHDRAWN',withdrawn_at=clock_timestamp(),withdraw_window_id=? WHERE enrollment_id=? AND status='ACTIVE'", f.withdrawWindow, episode)
        if (changed == 1) assertEquals(1, c.execute("UPDATE sections.course_sections SET enrolled_count=enrolled_count-1 WHERE section_id=? AND enrolled_count>0", f.section))
        changed
    }
}
