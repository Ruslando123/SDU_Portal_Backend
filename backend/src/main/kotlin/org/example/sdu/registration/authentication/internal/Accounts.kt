package org.example.sdu.registration.authentication.internal

import org.example.sdu.registration.authentication.api.Actor
import org.example.sdu.registration.authentication.api.CurrentActor
import org.example.sdu.registration.shared.database.*
import org.example.sdu.registration.shared.errors.ApiFailure
import org.springframework.security.core.CredentialsContainer
import org.springframework.security.core.authority.SimpleGrantedAuthority
import org.springframework.security.core.context.SecurityContextHolder
import org.springframework.security.core.userdetails.UserDetails
import org.springframework.security.core.userdetails.UserDetailsService
import org.springframework.security.core.userdetails.UsernameNotFoundException
import org.springframework.stereotype.Service
import java.io.Serializable
import java.util.UUID

/** Password hashes are transient and erased before the security context is saved. */
class AccountPrincipal(val actor: Actor, private val enabled: Boolean, @Transient private var hash: String?) : UserDetails, CredentialsContainer, Serializable {
    override fun getUsername(): String = actor.principalId.toString()
    override fun getPassword(): String? = hash
    override fun getAuthorities() = listOf(SimpleGrantedAuthority("ROLE_${actor.role}"))
    override fun isEnabled(): Boolean = enabled
    override fun eraseCredentials() { hash = null }
    companion object { private const val serialVersionUID = 1L }
}

@Service
class Accounts(private val sql: Sql, securityReads: SecurityAccountReads?) : UserDetailsService, CurrentActor {
    private val accountValidation = securityReads?.sql ?: sql
    override fun loadUserByUsername(username: String): UserDetails {
        if (username.length !in 1..100) throw UsernameNotFoundException("Invalid credentials")
        val row = sql.one("SELECT * FROM identity.test_accounts WHERE login_name=?", username.lowercase())
            ?: throw UsernameNotFoundException("Invalid credentials")
        return AccountPrincipal(actor(row), row["enabled"] as Boolean, row.text("password_hash"))
    }
    fun valid(principal: AccountPrincipal): Boolean {
        val row = accountValidation.one("SELECT principal_id,student_id,role,auth_version,enabled FROM identity.test_accounts WHERE principal_id=?", principal.actor.principalId) ?: return false
        return row["enabled"] == true && actor(row) == principal.actor
    }
    override fun authenticated(): Actor = (SecurityContextHolder.getContext().authentication?.principal as? AccountPrincipal)?.actor
        ?: throw ApiFailure(401, "AUTHENTICATION_REQUIRED")
    override fun student(): Actor = authenticated().also {
        if (it.role != "STUDENT" || it.studentId == null) throw ApiFailure(403, "FORBIDDEN")
    }
    private fun actor(row: Row) = Actor(row.uuid("principal_id"), row["student_id"] as UUID?, row.text("role"), (row["auth_version"] as Number).toLong())
}
