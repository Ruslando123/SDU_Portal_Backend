package org.example.sdu.registration.bootstrap.database

import org.flywaydb.core.api.callback.Callback
import org.flywaydb.core.api.callback.Context
import org.flywaydb.core.api.callback.Event

/** Local bootstrap credentials are bound and quoted by PostgreSQL, never SQL placeholders. */
class RuntimeCredentialsCallback(private val password: String) : Callback {
    init { require(password.isNotBlank()) { "DB_RUNTIME_PASSWORD must not be blank" } }

    override fun supports(event: Event, context: Context?): Boolean = event == Event.AFTER_MIGRATE
    override fun canHandleInTransaction(event: Event, context: Context?): Boolean = true
    override fun getCallbackName(): String = "configure-local-runtime-credentials"

    override fun handle(event: Event, context: Context) {
        context.connection.prepareStatement(
            "SELECT pg_catalog.format('ALTER ROLE portal_runtime LOGIN PASSWORD %L', CAST(? AS text))"
        ).use { statement ->
            statement.setString(1, password)
            statement.executeQuery().use { result ->
                check(result.next())
                context.connection.createStatement().use { it.execute(result.getString(1)) }
            }
        }
    }
}
