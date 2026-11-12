package org.example.sdu.verification

import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path

class ModuleBoundaryTest {
    @Test
    fun `production modules do not import another modules internal implementation`() {
        val source=Path.of(System.getProperty("project.root"),"backend/src/main/kotlin")
        val violations=mutableListOf<String>()
        val allowed=mapOf(
            "authentication" to setOf("students"), "students" to emptySet(), "catalog" to emptySet(),
            "sections" to setOf("catalog","authentication"), "academicrules" to emptySet(),
            "enrollment" to setOf("authentication","students","catalog","sections","academicrules"),
            "schedule" to setOf("enrollment","catalog","sections"),
            "studentportal" to setOf("authentication"),

            "administration" to setOf("students","catalog","sections","academicrules","enrollment"),
            "shared" to emptySet()
        )
        Files.walk(source).use { files -> files.filter { it.toString().endsWith(".kt") }.forEach { path ->
            val text=Files.readString(path)
            val owner=Regex("(?m)^package org\\.example\\.sdu\\.registration\\.(\\w+)").find(text)?.groupValues?.get(1)
            Regex("(?m)^import org\\.example\\.sdu\\.registration\\.(\\w+)\\.api\\.([A-Za-z*]+)").findAll(text).forEach { imported ->
                val target=imported.groupValues[1]
                if(target!=owner && target in allowed && owner in allowed && target !in allowed.getValue(owner!!)) violations += "$path: forbidden edge $owner -> $target"
                val contract=imported.groupValues[2]
                if(contract=="SectionAllocation" && owner !in setOf("sections","enrollment")) violations += "$path: forbidden allocation caller"
                if(contract=="EnrollmentCommands" && owner !in setOf("enrollment")) violations += "$path: forbidden enrollment-command caller"
            }
            Regex("(?m)^import org\\.example\\.sdu\\.registration\\.(\\w+)\\.internal\\.").findAll(text).forEach { imported ->
                if(imported.groupValues[1]!=owner) violations += "$path: ${imported.value}"
            }
        } }
        assertTrue(violations.isEmpty(),violations.joinToString("\n"))
    }
}
