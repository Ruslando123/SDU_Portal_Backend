package org.example.sdu.verification

import org.example.sdu.registration.shared.errors.ApiFailure
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import java.time.Duration

class EnrollmentDeadlineTest {
    @Test fun `total deadline bounds repeated serialization failures in fresh transactions`() {
        Phase5Harness("--enrollment.deadline-ms=1000","--enrollment.lock-timeout-ms=100","--enrollment.statement-timeout-ms=500","--enrollment.max-attempts=5").use { app ->
            val f=Fixture().populate(app.db)
            app.db.connection().use { c ->
                c.execute("CREATE SEQUENCE public.deadline_attempts")
                c.execute("GRANT USAGE,SELECT ON SEQUENCE public.deadline_attempts TO portal_runtime")
                c.execute("""
                    CREATE FUNCTION public.deadline_failure() RETURNS trigger LANGUAGE plpgsql AS '
                    BEGIN PERFORM nextval(''public.deadline_attempts''); PERFORM pg_sleep(0.35);
                    RAISE EXCEPTION USING ERRCODE=''40001'', MESSAGE=''Synthetic serialization failure''; END'
                """)
                c.execute("CREATE TRIGGER deadline_failure BEFORE INSERT ON enrollment.enrollments FOR EACH ROW EXECUTE FUNCTION public.deadline_failure()")
            }
            val start=System.nanoTime()
            val failure=assertThrows(ApiFailure::class.java) {app.enroll(f)}
            assertEquals(503,failure.status)
            assertTrue(Duration.ofNanos(System.nanoTime()-start).toMillis()<2200)
            assertTrue(app.db.count("SELECT last_value FROM public.deadline_attempts") in 1..3)
            assertEquals(0L,app.db.count("SELECT count(*) FROM operations.idempotency_records"))
            Phase5InvariantChecks.verify(app.db)
        }
    }
}
