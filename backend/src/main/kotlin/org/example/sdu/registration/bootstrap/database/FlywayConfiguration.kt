package org.example.sdu.registration.bootstrap.database

import org.flywaydb.core.api.callback.Callback
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Configuration
import org.springframework.core.env.Environment

@Configuration(proxyBeanMethods = false)
class FlywayConfiguration {
    @Bean
    fun runtimeCredentialsCallback(environment: Environment): Callback =
        RuntimeCredentialsCallback(environment.getRequiredProperty("DB_RUNTIME_PASSWORD"))
}
