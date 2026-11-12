package org.example.sdu.registration.authentication.internal.provisioning

import org.springframework.security.crypto.factory.PasswordEncoderFactories
import java.sql.DriverManager
import java.util.UUID

/** Offline operator setup only; never registered as a web endpoint or application runner. */
object SyntheticAccountCli {
    @JvmStatic fun main(args: Array<String>) {
        require(System.getenv("SPRING_PROFILES_ACTIVE") in setOf("dev", "test")) { "Explicit isolated dev/test profile required" }
        fun required(name: String): String = requireNotNull(System.getenv(name)) { "Missing $name" }
        val login = required("SYNTHETIC_LOGIN")
        require(login.length in 1..100 && login == login.lowercase())
        val role = required("SYNTHETIC_ROLE");require(role in setOf("STUDENT", "ADMIN"))
        val student = if (role == "STUDENT") UUID.fromString(required("SYNTHETIC_STUDENT_ID")) else null
        val chars = System.getenv("SYNTHETIC_PASSWORD")?.toCharArray()
            ?: requireNotNull(System.console()) { "Use a terminal password prompt or supply SYNTHETIC_PASSWORD outside Git" }.readPassword("Synthetic password: ")
        require(chars.size in 16..72 && String(chars).toByteArray(Charsets.UTF_8).size <= 72) { "Use a unique synthetic password of 16 to 72 characters (at most 72 UTF-8 bytes)" }
        val hash = try { PasswordEncoderFactories.createDelegatingPasswordEncoder().encode(String(chars)) } finally { chars.fill('\u0000') }
        // Migrator credentials are isolated from the running service and never logged.
        DriverManager.getConnection(required("DB_URL"), required("DB_MIGRATION_USERNAME"), required("DB_MIGRATION_PASSWORD")).use { connection ->
            connection.autoCommit = false
            try {
                connection.prepareStatement("INSERT INTO identity.test_accounts (principal_id,student_id,login_name,password_hash,role,enabled,auth_version,created_at) VALUES (?,?,?,?,?,true,1,clock_timestamp())").use { statement ->
                    statement.setObject(1, UUID.randomUUID());statement.setObject(2, student);statement.setString(3, login)
                    statement.setString(4, hash);statement.setString(5, role);statement.executeUpdate()
                }
                connection.commit()
            } catch (_: Exception) {
                connection.rollback();error("Synthetic account setup failed; check mapping and unique login using the operator connection.")
            }
        }
        println("Synthetic account created. No credential was printed.")
    }
}
