package org.example.sdu.registration.enrollment.internal.memory

import org.example.sdu.registration.shared.idempotency.Operation
import org.example.sdu.registration.shared.idempotency.StoredOutcome
import tools.jackson.databind.json.JsonMapper
import java.time.Instant
import java.util.UUID

/** Versioned final decision, not a request to be re-evaluated later by the projector. */
data class MemoryEvent(val id: UUID, val operation: Operation, val student: UUID, val time: Instant,
    val status: Int, val code: String, val body: Map<String, Any?>, val enrollment: UUID? = null,
    val section: UUID? = null, val term: UUID? = null, val offering: UUID? = null,
    val policy: UUID? = null, val window: UUID? = null) {
    fun outcome(mapper: JsonMapper) = StoredOutcome(status, code, mapper.writeValueAsString(body))
    fun encode(mapper: JsonMapper): ByteArray = mapper.writeValueAsBytes(mapOf(
        "version" to 1, "id" to id.toString(), "principal" to operation.principal.toString(),
        "type" to operation.type, "target" to operation.target.toString(), "key" to operation.key.toString(),
        "student" to student.toString(), "time" to time.toString(), "status" to status, "code" to code, "body" to body,
        "enrollment" to enrollment?.toString(), "section" to section?.toString(), "term" to term?.toString(),
        "offering" to offering?.toString(), "policy" to policy?.toString(), "window" to window?.toString()))
    companion object {
        fun decode(bytes: ByteArray, mapper: JsonMapper): MemoryEvent {
            require(bytes.size <= 32_768)
            val node = mapper.readTree(bytes)
            require(node.path("version").asInt() == 1 && node.path("type").asString() in setOf("ENROLL", "WITHDRAW"))
            fun id(name: String): UUID = UUID.fromString(node.path(name).asString())
            fun optional(name: String): UUID? = if (node.path(name).isNull) null else id(name)
            val body = node.path("body").properties().associate { it.key to if (it.value.isNull) null else if (it.value.isBoolean) it.value.asBoolean() else it.value.asString() }
            return MemoryEvent(id("id"), Operation(id("principal"), node.path("type").asString(), id("target"), id("key")), id("student"),
                Instant.parse(node.path("time").asString()), node.path("status").asInt(), node.path("code").asString(), body,
                optional("enrollment"), optional("section"), optional("term"), optional("offering"), optional("policy"), optional("window"))
        }
    }
}
