package org.example.sdu.registration.shared.idempotency

import org.example.sdu.registration.shared.database.*
import org.example.sdu.registration.shared.errors.ApiFailure
import org.springframework.dao.DataAccessException
import org.springframework.stereotype.Component
import org.springframework.transaction.annotation.Propagation
import org.springframework.transaction.annotation.Transactional
import tools.jackson.databind.json.JsonMapper
import java.security.MessageDigest
import java.time.Instant
import java.util.UUID

data class StoredOutcome(val status: Int, val code: String, val body: String, val replayed: Boolean = false)

class ClaimWait(cause: Throwable) : RuntimeException(cause)
data class Operation(val principal: UUID, val type: String, val target: UUID, val key: UUID) {
    val targetType: String get() = if (type == "ENROLL") "SECTION" else "ENROLLMENT"
    fun fingerprint(): ByteArray = MessageDigest.getInstance("SHA-256").digest("sdu:v1:$type:$targetType:$target".toByteArray(Charsets.UTF_8))
}
data class Claim(val operationId: UUID?, val replay: StoredOutcome?)

@Component
@Transactional(propagation = Propagation.MANDATORY)
class DurableOutcomes(private val sql: Sql, private val mapper: JsonMapper) {
    fun claim(operation: Operation): Claim {
        // Repetition only handles cleanup visibility races; all waits have a PostgreSQL statement deadline.
        repeat(8) {
            val id = UUID.randomUUID()
            val inserted = try {
                sql.update("""
                    INSERT INTO operations.idempotency_records
                    (operation_id,principal_id,operation_type,operation_version,idempotency_key,request_fingerprint,target_type,target_id,state,created_at)
                    VALUES (?,?,?,1,?,?,?,?,'CLAIMED',clock_timestamp())
                    ON CONFLICT (principal_id,operation_type,operation_version,idempotency_key) DO NOTHING
                """, id, operation.principal, operation.type, operation.key, operation.fingerprint(), operation.targetType, operation.target)
            } catch (failure: DataAccessException) { throw ClaimWait(failure) }
            if (inserted == 1) return Claim(id, null)
            // A separate statement is essential under READ COMMITTED after the conflicting INSERT waited.
            val row = try {
                sql.one("""
                    SELECT *,response_body::text AS body FROM operations.idempotency_records
                    WHERE principal_id=? AND operation_type=? AND operation_version=1 AND idempotency_key=? FOR UPDATE
                """, operation.principal, operation.type, operation.key)
            } catch (failure: DataAccessException) { throw ClaimWait(failure) }
            if (row != null) {
                check(row["state"] == "COMPLETED") { "Nonterminal committed operation" }
                if (!sql.clock().isBefore(row.instant("expires_at"))) {
                    check(sql.one("SELECT operations.delete_expired_outcome(?) AS deleted", row.uuid("operation_id"))?.get("deleted") == true)
                } else {
                    if (!MessageDigest.isEqual(row["request_fingerprint"] as ByteArray, operation.fingerprint()) ||
                        row["target_type"] != operation.targetType || row["target_id"] != operation.target) throw ApiFailure(409, "IDEMPOTENCY_KEY_REUSED")
                    return Claim(null, StoredOutcome((row["response_status"] as Number).toInt(), row.text("response_code"), row.text("body"), true))
                }
            }
        }
        throw ApiFailure(409, "REQUEST_IN_PROGRESS", true)
    }

    fun complete(id: UUID, status: Int, code: String, body: Map<String, Any?>, decidedAt: Instant, retentionSeconds: Long): StoredOutcome {
        val result = requireNotNull(sql.one("""
            UPDATE operations.idempotency_records SET state='COMPLETED',decided_at=?,response_status=?,response_code=?,
                response_body=CAST(? AS jsonb),expires_at=? WHERE operation_id=? AND state='CLAIMED'
            RETURNING response_body::text AS body
        """, decidedAt, status, code, mapper.writeValueAsString(body), decidedAt.plusSeconds(retentionSeconds), id))
        return StoredOutcome(status, code, result.text("body"))
    }
}
