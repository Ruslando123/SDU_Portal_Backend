package org.example.sdu.verification

import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import java.util.UUID

class IdempotencyIntegrationTest : PostgresTest() {
    @Test
    fun `unfinished claim cannot commit and does not survive rollback`() {
        val f = Fixture().populate(db)
        expectSqlState("23514") { db.transaction { f.claim(it) } }
        assertEquals(0L, db.count("SELECT count(*) FROM operations.idempotency_records"))
    }

    @Test
    fun `claim completion inspects final row and terminal response is immutable`() {
        val f = Fixture().populate(db)
        val operation = db.transaction { c -> f.claim(c).also { f.complete(c, it) } }
        assertEquals("COMPLETED", db.value("SELECT state FROM operations.idempotency_records WHERE operation_id=?", operation))
        assertEquals("SECTION_FULL", db.value("SELECT response_code FROM operations.idempotency_records WHERE operation_id=?", operation))
        expectSqlState("23514") { db.transaction { it.execute("UPDATE operations.idempotency_records SET response_code='CHANGED' WHERE operation_id=?", operation) } }
        assertEquals("SECTION_FULL", db.value("SELECT response_code FROM operations.idempotency_records WHERE operation_id=?", operation))
    }

    @Test
    fun `scope uniqueness applies even when another payload is attempted`() {
        val f = Fixture().populate(db); val key = UUID.randomUUID()
        db.transaction { c -> f.claim(c, key).also { f.complete(c, it) } }
        expectSqlState("23505") { db.transaction { f.claim(it, key) } }
        assertEquals(1L, db.count("SELECT count(*) FROM operations.idempotency_records"))
    }

    @Test
    fun `claim identity cannot be rewritten and collection targets can be NULL`() {
        val f = Fixture().populate(db)
        expectSqlState("23514") {
            db.transaction { c -> val op = f.claim(c); c.execute("UPDATE operations.idempotency_records SET request_fingerprint=? WHERE operation_id=?", ByteArray(32) { 3 }, op) }
        }
        val op = db.transaction { c -> f.claim(c, collection = true).also { f.complete(c, it) } }
        assertNull(db.value("SELECT target_id FROM operations.idempotency_records WHERE operation_id=?", op))
        assertEquals("COURSE_COLLECTION", db.value("SELECT target_type FROM operations.idempotency_records WHERE operation_id=?", op))
    }

    @Test
    fun `failure before commit rolls back count episode audit and completed result`() {
        val f = Fixture().populate(db)
        assertThrows(IllegalStateException::class.java) {
            db.transaction { c ->
                val operation = f.claim(c); f.lock(c)
                c.execute("UPDATE sections.course_sections SET enrolled_count=enrolled_count+1 WHERE section_id=? AND enrolled_count<capacity", f.section)
                val episode = f.rawEpisode(c)
                c.execute("INSERT INTO audit.audit_events VALUES (?,?,?,'ENROLLED','ENROLLMENT',?,?,clock_timestamp(),?,'{}')", UUID.randomUUID(), operation, f.principal, episode, episode, UUID.randomUUID())
                f.complete(c, operation)
                error("Injected failure before commit")
            }
        }
        assertEquals(0L, db.count("SELECT count(*) FROM enrollment.enrollments"))
        assertEquals(0L, db.count("SELECT count(*) FROM audit.audit_events"))
        assertEquals(0L, db.count("SELECT count(*) FROM operations.idempotency_records"))
    }

    @Test
    fun `audit integrity failure aborts previously allocated seat and claim`() {
        val f = Fixture().populate(db)
        expectSqlState("23503") {
            db.transaction { c ->
                val operation = f.claim(c); f.lock(c)
                c.execute("UPDATE sections.course_sections SET enrolled_count=enrolled_count+1 WHERE section_id=?", f.section)
                val episode = f.rawEpisode(c)
                c.execute("INSERT INTO audit.audit_events VALUES (?,?,?,'ENROLLED','ENROLLMENT',?,?,clock_timestamp(),?,'{}')", UUID.randomUUID(), operation, UUID.randomUUID(), episode, episode, UUID.randomUUID())
                f.complete(c, operation)
            }
        }
        assertEquals(0L, db.count("SELECT count(*) FROM enrollment.enrollments"))
        assertEquals(0L, db.count("SELECT count(*) FROM operations.idempotency_records"))
    }

    @Test
    fun `guarded expiry helper preserves unexpired outcomes and audit history`() {
        val f = Fixture().populate(db)
        val live = db.transaction { c -> f.claim(c).also { f.complete(c, it) } }
        val expired = db.transaction { c -> f.claim(c, historical = true).also { f.complete(c, it, expired = true) } }
        db.transaction { c -> c.execute("INSERT INTO audit.audit_events VALUES (?,?,?,'ENROLLMENT_DENIED','SECTION',?,NULL,clock_timestamp(),?,'{}')", UUID.randomUUID(), expired, f.principal, f.section, UUID.randomUUID()) }
        db.connection(runtime = true).use { c ->
            assertEquals(false, c.value("SELECT operations.delete_expired_outcome(?)", live))
            assertEquals(true, c.value("SELECT operations.delete_expired_outcome(?)", expired))
        }
        assertEquals(1L, db.count("SELECT count(*) FROM operations.idempotency_records"))
        assertEquals(1L, db.count("SELECT count(*) FROM audit.audit_events WHERE operation_id=?", expired))
    }

    @Test
    fun `cleanup bounds reject unbounded work and claims cannot be deleted`() {
        val f = Fixture().populate(db)
        expectSqlState("22023") { db.transaction { it.value("SELECT operations.cleanup_expired_outcomes(0)") } }
        expectSqlState("22023") { db.transaction { it.value("SELECT operations.cleanup_expired_outcomes(1001)") } }
        db.transaction { c ->
            val op = f.claim(c)
            assertEquals(false, c.value("SELECT operations.delete_expired_outcome(?)", op))
            f.complete(c, op)
        }
    }
}
