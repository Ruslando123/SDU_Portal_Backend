package org.example.sdu.verification

import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Tag
import org.junit.jupiter.api.Test
import java.util.UUID
import java.util.concurrent.Callable
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.nio.file.Files
import java.nio.file.Path

@Tag("correctness-stress")
class CorrectnessStressTest {
    @Test fun `2000 distinct eligible students complete allocation of exactly 30 seats`() {
        Phase5Harness("--spring.datasource.hikari.maximum-pool-size=16", "--spring.datasource.hikari.connection-timeout=3000").use { app ->
            val seed=Fixture().populate(app.db,30)
            val students=mutableListOf(seed)
            app.db.transaction { c ->
                repeat(1999) {
                    val next=seed.copy(student=UUID.randomUUID(),principal=UUID.randomUUID(),snapshot=UUID.randomUUID())
                    c.execute("INSERT INTO students.students VALUES (?,?,'Synthetic Stress Student',clock_timestamp())",next.student,"synthetic-${next.student}")
                    c.execute("INSERT INTO identity.test_accounts VALUES (?,?,?,?,'STUDENT',true,1,clock_timestamp())",next.principal,next.student,"test-${next.principal}",TestPostgres.syntheticVerifier)
                    c.execute("INSERT INTO students.eligibility_snapshots VALUES (?,?,'stress-v1','COMPLETE','ACTIVE',clock_timestamp())",next.snapshot,next.student)
                    c.execute("INSERT INTO enrollment.student_terms VALUES (?,?,?,'TEST',?,NULL,clock_timestamp())",next.student,seed.term,seed.policy,next.snapshot)
                    students+=next
                }
            }
            val executor=Executors.newFixedThreadPool(System.getProperty("correctness.stress.workers", "32").toInt())
            val ready=CountDownLatch(System.getProperty("correctness.stress.workers", "32").toInt());val go=CountDownLatch(1)
            val started=System.nanoTime()
            try {
                val requests=students.map { student -> executor.submit(Callable {
                    ready.countDown();check(go.await(10,TimeUnit.SECONDS))
                    try { app.enroll(student).code } catch (failure:Exception) { "TECHNICAL_${failure.javaClass.simpleName}" }
                }) }
                assertTrue(ready.await(10,TimeUnit.SECONDS));go.countDown()
                val results=requests.map { it.get(120,TimeUnit.SECONDS) }.groupingBy { it }.eachCount()
                val elapsed=(System.nanoTime()-started)/1_000_000
                Phase5InvariantChecks.verify(app.db)
                assertEquals(30L,app.db.count("SELECT count(DISTINCT student_id) FROM enrollment.enrollments WHERE section_id=? AND status='ACTIVE'",seed.section))
                assertEquals(30L,app.db.count("SELECT count(*) FROM audit.audit_events WHERE event_type='ENROLLED'"))
                assertEquals(1970L,app.db.count("SELECT count(*) FROM operations.idempotency_records WHERE response_code='SECTION_FULL'"))
                assertEquals(2000L,app.db.count("SELECT count(*) FROM operations.idempotency_records WHERE operation_type='ENROLL'"))
                val report="""
                    boundary=public EnrollmentCommands with real Authentication principal resolver, Spring transaction proxies, runtime Hikari pool and PostgreSQL
                    students=2000
                    capacity=30
                    workers=${System.getProperty("correctness.stress.workers", "32")}
                    maximum_pool_size=16
                    outcomes=$results
                    elapsed_ms=$elapsed
                    invariant_violations=0
                    technical_failures=${results.filterKeys { it !in setOf("ENROLLED","SECTION_FULL") }.values.sum()}
                """.trimIndent()+"\n"
                val destination=Path.of(System.getProperty("project.root"),"backend/build/reports/phase5-correctness-stress.txt")
                Files.createDirectories(destination.parent);Files.writeString(destination,report)
                println(report)
                assertEquals(mapOf("ENROLLED" to 30,"SECTION_FULL" to 1970),results)
            } finally { go.countDown();executor.shutdownNow();assertTrue(executor.awaitTermination(15,TimeUnit.SECONDS)) }
        }
    }
}
