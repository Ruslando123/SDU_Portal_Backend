package org.example.sdu.registration.bootstrap.database

import org.flywaydb.core.Flyway

object MigrationCli {
    @JvmStatic
    fun main(args: Array<String>) {
        val result = Flyway.configure()
            .dataSource(required("DB_URL"), required("DB_MIGRATION_USERNAME"), required("DB_MIGRATION_PASSWORD"))
            .defaultSchema("public")
            .locations("classpath:db/migration")
            .cleanDisabled(true)
            .validateMigrationNaming(true)
            .callbacks(RuntimeCredentialsCallback(required("DB_RUNTIME_PASSWORD")))
            .load().migrate()
        println("Flyway migration complete: ${result.migrationsExecuted} migrations applied.")
    }

    private fun required(name: String): String =
        System.getenv(name)?.takeIf { it.isNotBlank() } ?: error("Missing environment variable: $name")
}
