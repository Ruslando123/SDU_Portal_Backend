package org.example.sdu.registration.shared.observability

import org.springframework.beans.factory.annotation.Value
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Configuration
import org.springframework.core.annotation.Order
import org.springframework.http.HttpMethod
import org.springframework.security.authentication.ProviderManager
import org.springframework.security.authentication.dao.DaoAuthenticationProvider
import org.springframework.security.config.annotation.web.builders.HttpSecurity
import org.springframework.security.config.http.SessionCreationPolicy
import org.springframework.security.core.userdetails.User
import org.springframework.security.crypto.password.PasswordEncoder
import org.springframework.security.provisioning.InMemoryUserDetailsManager
import org.springframework.security.web.SecurityFilterChain

/** Separate read-only management listener; its credential cannot authenticate a student API. */
@Configuration(proxyBeanMethods = false)
@ConditionalOnProperty(name = ["portal.monitoring.enabled"], havingValue = "true")
class MonitoringConfiguration {
    @Bean @Order(-10)
    fun monitoringSecurity(http: HttpSecurity, encoder: PasswordEncoder,
        @Value("\${management.server.port}") port: Int,
        @Value("\${PORTAL_METRICS_PASSWORD}") password: String): SecurityFilterChain {
        require(password.length >= 24)
        val users = InMemoryUserDetailsManager(User.withUsername("metrics").password(encoder.encode(password)).roles("MONITOR").build())
        val provider = DaoAuthenticationProvider(users).also { it.setPasswordEncoder(encoder) }
        http.securityMatcher { request -> request.localPort == port && request.requestURI == "/actuator/prometheus" }
            .authenticationManager(ProviderManager(listOf(provider)))
            .authorizeHttpRequests { it.requestMatchers(HttpMethod.GET, "/actuator/prometheus").hasRole("MONITOR").anyRequest().denyAll() }
            .httpBasic { }.sessionManagement { it.sessionCreationPolicy(SessionCreationPolicy.STATELESS) }
            .requestCache { it.disable() }.csrf { it.disable() }
        return http.build()
    }
}
