package org.example.sdu.verification

import org.example.sdu.registration.shared.errors.ApiFailure
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import java.time.Duration
import java.util.concurrent.Callable
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

class EnrollmentAdmissionTest {
    @Test fun `queued enrollment waits outside JDBC while another checkout remains available`() {
        Phase5Harness("--spring.datasource.hikari.maximum-pool-size=2", "--enrollment.jdbc-admission-enabled=true",
            "--enrollment.jdbc-admission-wait-ms=500").use { app ->
            val firstStudent = Fixture().populate(app.db)
            val secondStudent = firstStudent.anotherStudent(app.db)
            app.db.connection().use { blocker ->
                blocker.autoCommit = false
                blocker.value("SELECT section_id FROM sections.course_sections WHERE section_id=? FOR UPDATE", firstStudent.section)
                val executor = Executors.newFixedThreadPool(2)
                try {
                    val first = executor.submit(Callable { app.enroll(firstStudent) })
                    val deadline = System.nanoTime() + Duration.ofSeconds(2).toNanos()
                    while (app.db.count("SELECT count(*) FROM pg_stat_activity WHERE datname=? AND usename='portal_runtime' AND wait_event_type='Lock' AND query LIKE '%sections.course_sections%'", app.db.name) == 0L) {
                        check(System.nanoTime() < deadline)
                        Thread.sleep(10)
                    }
                    val saturated = assertThrows(ApiFailure::class.java) { app.enroll(secondStudent) }
                    assertEquals("SERVICE_OVERLOADED", saturated.code)
                    assertEquals(0L, app.db.count("SELECT count(*) FROM operations.idempotency_records WHERE principal_id=?", secondStudent.principal))
                    val submitted = CountDownLatch(1)
                    val second = executor.submit(Callable {
                        app.asStudent(secondStudent) {
                            submitted.countDown()
                            app.commands.enroll(secondStudent.section, java.util.UUID.randomUUID().toString())
                        }
                    })
                    assertTrue(submitted.await(1, TimeUnit.SECONDS))
                    // Keep the spare checkout while the first command still holds its transaction.
                    app.context.getBean(javax.sql.DataSource::class.java).connection.use {
                        assertTrue(it.isValid(1))
                        assertFalse(second.isDone)
                        assertEquals(1L, app.db.count("SELECT count(*) FROM pg_stat_activity WHERE datname=? AND usename='portal_runtime' AND xact_start IS NOT NULL", app.db.name))
                    }
                    blocker.commit()
                    assertEquals("ENROLLED", first.get(5, TimeUnit.SECONDS).code)
                    assertEquals("ENROLLED", second.get(5, TimeUnit.SECONDS).code)
                } finally {
                    blocker.rollback()
                    executor.shutdownNow()
                }
            }
            Phase5InvariantChecks.verify(app.db)
        }
    }

    @Test fun `bounded admission rejects excess work outside the root transaction`() {
        Phase5Harness("--enrollment.admission=1").use { app ->
            val f=Fixture().populate(app.db);val other=f.anotherStudent(app.db)
            app.db.connection().use { blocker ->
                blocker.autoCommit=false;blocker.value("SELECT section_id FROM sections.course_sections WHERE section_id=? FOR UPDATE",f.section)
                val executor=Executors.newSingleThreadExecutor()
                try {
                    val first=executor.submit(Callable {app.enroll(f)})
                    val deadline=System.nanoTime()+Duration.ofSeconds(2).toNanos()
                    while(app.db.count("SELECT count(*) FROM pg_stat_activity WHERE datname=? AND usename='portal_runtime' AND wait_event_type='Lock' AND query LIKE '%sections.course_sections%'",app.db.name)==0L) {
                        check(System.nanoTime()<deadline);Thread.sleep(10)
                    }
                    val failure=assertThrows(ApiFailure::class.java) {app.enroll(other)}
                    assertEquals("SERVICE_OVERLOADED",failure.code);assertEquals(503,failure.status)
                    blocker.commit();assertEquals("ENROLLED",first.get(8,TimeUnit.SECONDS).code)
                } finally {blocker.rollback();executor.shutdownNow()}
            }
            assertEquals(0L,app.db.count("SELECT count(*) FROM operations.idempotency_records WHERE principal_id=?",other.principal))
            Phase5InvariantChecks.verify(app.db)
        }
    }
}
