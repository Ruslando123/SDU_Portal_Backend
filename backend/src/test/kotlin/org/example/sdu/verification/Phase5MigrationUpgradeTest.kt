package org.example.sdu.verification

import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class Phase5MigrationUpgradeTest {
    @Test fun `existing populated V6 database upgrades with a narrow runtime lock capability`() {
        TestDatabase.create("6").use { db ->
            val f=Fixture().populate(db)
            val before=db.value("SELECT string_agg(version||':'||checksum,',' ORDER BY installed_rank) FROM public.flyway_schema_history")
            expectSqlState("42501") { db.transaction(true) { f.lock(it,sectionLock=false) } }
            assertEquals(1,db.flyway("7").migrate().migrationsExecuted)
            assertEquals(before,db.value("SELECT string_agg(version||':'||checksum,',' ORDER BY installed_rank) FROM public.flyway_schema_history WHERE version::int<=6"))
            db.transaction(true) { c ->
                assertEquals(f.policy,c.value("SELECT policy_id FROM enrollment.lock_student_term(?,?)",f.student,f.term))
                assertFalse(c.value("SELECT has_column_privilege(current_user,'enrollment.student_terms','credit_limit_override','UPDATE')") as Boolean)
            }
            assertEquals(1L,db.count("SELECT count(*) FROM enrollment.student_terms WHERE student_id=?",f.student))
            assertTrue(db.flyway("7").validateWithResult().validationSuccessful);db.invariants()
        }
    }
}
