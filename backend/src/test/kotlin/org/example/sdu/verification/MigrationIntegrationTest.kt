package org.example.sdu.verification

import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path

class MigrationIntegrationTest : PostgresTest() {
    @Test
    fun `empty database migrates and a second validated run applies nothing`() {
        assertEquals(8L, db.count("SELECT count(*) FROM public.flyway_schema_history WHERE success"))
        assertEquals(0, db.flyway().migrate().migrationsExecuted)
        assertTrue(db.flyway().validateWithResult().validationSuccessful)
        assertEquals("18.6", db.value("SHOW server_version"))
    }

    @Test
    fun `physical columns keys checks and indexes match the English dictionary`() {
        val source = Files.readString(Path.of(System.getProperty("project.root"), "docs/database.md"))
        val headers = Regex("(?m)^### DBT-\\d{2} — ([a-z_]+\\.[a-z_]+)$").findAll(source).toList()
        assertEquals(22, headers.size)
        val expectedTables = headers.map { it.groupValues[1] }.toSet()
        val expectedIndexes = mutableSetOf<String>()
        db.connection().use { c ->
            val actualTables = mutableSetOf<String>()
            c.createStatement().use { s -> s.executeQuery("SELECT table_schema||'.'||table_name FROM information_schema.tables WHERE table_type='BASE TABLE' AND table_schema IN ('students','identity','catalog','rules','sections','enrollment','operations','audit')").use { r -> while (r.next()) actualTables += r.getString(1) } }
            assertEquals(expectedTables, actualTables)
            headers.forEachIndexed { index, header ->
                val table = header.groupValues[1]
                val (schema, name) = table.split('.')
                val body = source.substring(header.range.last + 1, headers.getOrNull(index + 1)?.range?.first ?: source.indexOf("## Critical enrollment constraints"))
                val columns = Regex("(?m)^\\| `([^`]+)` \\| `([^`]+)` \\| (NN|Nullable) \\|").findAll(body).toList()
                val actualColumns = mutableMapOf<String, List<String?>>()
                c.prepareStatement("SELECT column_name,data_type,is_nullable,character_maximum_length,numeric_precision,numeric_scale,collation_name,column_default FROM information_schema.columns WHERE table_schema=? AND table_name=?").use { s ->
                    s.setString(1, schema); s.setString(2, name)
                    s.executeQuery().use { r -> while (r.next()) actualColumns[r.getString(1)] = (2..8).map { r.getString(it) } }
                }
                assertEquals(columns.map { it.groupValues[1] }.toSet(), actualColumns.keys, table)
                columns.forEach { column ->
                    val col = column.groupValues[1]; val type = column.groupValues[2]; val actual = actualColumns.getValue(col)
                    val sqlType = when {
                        type.startsWith("varchar") -> "character varying"
                        type.startsWith("char(") -> "character"
                        type == "timestamptz" -> "timestamp with time zone"
                        else -> type.substringBefore('(').substringBefore(" COLLATE")
                    }
                    assertEquals(sqlType, actual[0], "$table.$col type")
                    assertEquals(if (column.groupValues[3] == "NN") "NO" else "YES", actual[1], "$table.$col nullability")
                    Regex("(?:var)?char\\((\\d+)\\)").matchEntire(type)?.let { assertEquals(it.groupValues[1], actual[2]) }
                    Regex("numeric\\((\\d+),(\\d+)\\)").matchEntire(type)?.let { assertEquals(it.groupValues[1], actual[3]); assertEquals(it.groupValues[2], actual[4]) }
                    if (type.contains("COLLATE")) assertEquals("C", actual[5])
                    if (table == "sections.course_sections" && col == "enrolled_count") assertEquals("0", actual[6])
                    else assertNull(actual[6], "$table.$col unexpected default")
                }
                val primary = Regex("\\*\\*Primary key:\\*\\* `(\\w+)` on `\\(([^)]+)\\)`").find(body)!!
                val uniqueLine = body.lineSequence().first { it.startsWith("- **Ordinary UNIQUE:") }
                val uniques = Regex("`(\\w+) \\(([^)]+)\\)`").findAll(uniqueLine).associate { it.groupValues[1] to it.groupValues[2] }
                val fkLine = body.lineSequence().first { it.startsWith("- **Foreign keys") }
                val fks = Regex("`(\\w+): \\(([^)]+)\\) → ([a-z_.]+) \\(([^)]+)\\)`, ON DELETE (NO ACTION|CASCADE)").findAll(fkLine).toList()
                val constraints = mutableMapOf<String, List<String>>()
                c.prepareStatement("""
                    SELECT x.conname,x.contype,
                      array_to_string(ARRAY(SELECT a.attname FROM unnest(x.conkey) WITH ORDINALITY k(n,o) JOIN pg_attribute a ON a.attrelid=x.conrelid AND a.attnum=k.n ORDER BY k.o),', '),
                      coalesce(pn.nspname||'.'||p.relname,''),
                      array_to_string(ARRAY(SELECT a.attname FROM unnest(x.confkey) WITH ORDINALITY k(n,o) JOIN pg_attribute a ON a.attrelid=x.confrelid AND a.attnum=k.n ORDER BY k.o),', '),
                      x.confdeltype,x.condeferrable,x.confmatchtype
                    FROM pg_constraint x JOIN pg_class t ON t.oid=x.conrelid JOIN pg_namespace n ON n.oid=t.relnamespace
                    LEFT JOIN pg_class p ON p.oid=x.confrelid LEFT JOIN pg_namespace pn ON pn.oid=p.relnamespace
                    WHERE n.nspname=? AND t.relname=? AND x.contype IN ('p','u','f','c')
                """).use { s ->
                    s.setString(1, schema); s.setString(2, name)
                    s.executeQuery().use { r -> while (r.next()) constraints[r.getString(1)] = (2..8).map { r.getString(it) ?: "" } }
                }
                assertEquals(listOf("p", primary.groupValues[2]), constraints.getValue(primary.groupValues[1]).take(2))
                expectedIndexes += primary.groupValues[1]
                assertEquals(uniques.keys, constraints.filterValues { it[0] == "u" }.keys, "$table UNIQUE names")
                uniques.forEach { (key, cols) -> assertEquals(cols, constraints.getValue(key)[1]); expectedIndexes += key }
                assertEquals(fks.map { it.groupValues[1] }.toSet(), constraints.filterValues { it[0] == "f" }.keys, "$table FKs")
                fks.forEach { fk ->
                    val actual = constraints.getValue(fk.groupValues[1])
                    assertEquals(listOf("f", fk.groupValues[2], fk.groupValues[3], fk.groupValues[4], if (fk.groupValues[5] == "CASCADE") "c" else "a"), actual.take(5))
                    assertEquals("s", actual[6], "MATCH SIMPLE")
                }
                constraints.values.forEach { assertEquals("f", it[5], "Ordinary constraints must be immediate") }
                val checkLine = body.lineSequence().first { it.startsWith("- **CHECK constraints:") }
                assertEquals(Regex("`[^`]+`").findAll(checkLine).count(), constraints.values.count { it[0] == "c" }, "$table CHECK count")
                val indexLine = body.lineSequence().first { it.startsWith("- **Indexes:") }
                Regex("`(\\w+) \\(([^)]+)\\)`(?: WHERE `([^`]+)`)?").findAll(indexLine).forEach { idx ->
                    val indexName = idx.groupValues[1]
                    if (indexName != "ix_offering_published_term") {
                        expectedIndexes += indexName
                        val definition = c.value("SELECT indexdef FROM pg_indexes WHERE schemaname=? AND indexname=?", schema, indexName).toString()
                        assertTrue(definition.contains("(${idx.groupValues[2]})"), definition)
                        if (idx.groupValues[3].isNotEmpty()) {
                            val predicate = definition.substringAfter(" WHERE ").replace("::text", "").replace("(", "").replace(")", "")
                            assertEquals(idx.groupValues[3], predicate)
                        }
                    }
                }
            }
            val actualIndexes = mutableSetOf<String>()
            c.createStatement().use { s -> s.executeQuery("SELECT indexname FROM pg_indexes WHERE schemaname IN ('students','identity','catalog','rules','sections','enrollment','operations','audit')").use { r -> while (r.next()) actualIndexes += r.getString(1) } }
            assertEquals(expectedIndexes, actualIndexes)
            assertEquals(50, actualIndexes.size)
            assertEquals(0L, (c.value("SELECT count(*) FROM pg_constraint c JOIN pg_index i ON i.indexrelid=c.conindid WHERE c.contype='f' AND i.indpred IS NOT NULL") as Number).toLong())
            assertEquals(true, c.value("SELECT condeferrable AND condeferred FROM pg_constraint WHERE conname='require_terminal_idempotency'"))
        }
    }
}
