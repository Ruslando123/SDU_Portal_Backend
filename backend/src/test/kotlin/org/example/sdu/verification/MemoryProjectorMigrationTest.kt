package org.example.sdu.verification
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.Assertions.*

class MemoryProjectorMigrationTest {
    @Test fun `V8 adds an invoker-only projection function without changing V1 through V7`() {
        TestDatabase.create("7").use { db ->
            val before=db.value("SELECT string_agg(version||':'||checksum,',' ORDER BY installed_rank) FROM public.flyway_schema_history")
            assertEquals(1,db.flyway().migrate().migrationsExecuted)
            assertEquals(before,db.value("SELECT string_agg(version||':'||checksum,',' ORDER BY installed_rank) FROM public.flyway_schema_history WHERE version::int<=7"))
            assertEquals(false,db.value("SELECT prosecdef FROM pg_proc WHERE oid='enrollment.project_memory_decision(jsonb,text,bytea,text,bigint)'::regprocedure"))
            assertEquals(false,db.value("SELECT has_function_privilege('portal_observer','enrollment.project_memory_decision(jsonb,text,bytea,text,bigint)','EXECUTE')"))
            assertEquals(true,db.value("SELECT has_function_privilege('portal_runtime','enrollment.project_memory_decision(jsonb,text,bytea,text,bigint)','EXECUTE')"))
        }
    }
}
