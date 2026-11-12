package org.example.sdu.registration.academicrules.internal

import org.example.sdu.registration.academicrules.api.*
import org.example.sdu.registration.shared.database.*
import org.springframework.stereotype.Service
import org.springframework.transaction.annotation.Propagation
import org.springframework.transaction.annotation.Transactional
import java.util.UUID

@Service
@Transactional(propagation = Propagation.MANDATORY)
class JdbcAcademicPolicy(private val sql: Sql) : AcademicPolicy {
    override fun protect(term: UUID): Boolean? = sql.one("SELECT configuration_state FROM rules.term_policy_gates WHERE term_id=? FOR SHARE", term)?.let { it["configuration_state"] == "PUBLISHED" }
    override fun policy(id: UUID): Policy? = sql.one("SELECT credit_limit FROM rules.registration_policies WHERE policy_id=?", id)?.let { Policy(it.decimal("credit_limit")) }
    override fun window(policy: UUID, operation: String): Window? = sql.one("SELECT * FROM rules.registration_windows WHERE policy_id=? AND operation_type=?", policy, operation)
        ?.let { Window(it.uuid("window_id"), it.instant("opens_at"), it.instant("closes_at"), it["is_closed"] as Boolean) }
    override fun allowed(offering: UUID, policy: UUID): Boolean = sql.one("SELECT offering_id FROM rules.offering_policy_eligibility WHERE offering_id=? AND policy_id=?", offering, policy) != null
}
