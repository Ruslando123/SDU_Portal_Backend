package org.example.sdu.registration.authentication.internal.web

import jakarta.servlet.FilterChain
import jakarta.servlet.http.HttpServletRequest
import jakarta.servlet.http.HttpServletResponse
import org.example.sdu.registration.authentication.internal.AccountPrincipal
import org.example.sdu.registration.authentication.internal.Accounts
import org.example.sdu.registration.shared.database.isTransientDependencyFailure
import org.example.sdu.registration.shared.errors.ProblemResponses
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Configuration
import org.springframework.http.HttpStatus
import org.springframework.http.MediaType
import org.springframework.security.config.annotation.web.builders.HttpSecurity
import org.springframework.security.core.context.SecurityContextHolder
import org.springframework.security.crypto.factory.PasswordEncoderFactories
import org.springframework.security.crypto.password.PasswordEncoder
import org.springframework.security.web.SecurityFilterChain
import org.springframework.security.web.csrf.CsrfFilter
import org.springframework.web.filter.OncePerRequestFilter
import tools.jackson.databind.json.JsonMapper
import java.util.concurrent.Semaphore
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicLong

@Configuration(proxyBeanMethods = false)
class SecurityConfiguration {
    @Bean fun passwordEncoder(): PasswordEncoder = PasswordEncoderFactories.createDelegatingPasswordEncoder()

    @Bean
    fun securityFilterChain(http: HttpSecurity, mapper: JsonMapper, accounts: Accounts, environment: org.springframework.core.env.Environment, metrics: org.example.sdu.registration.shared.observability.EnrollmentMetrics): SecurityFilterChain {
        require(environment.acceptsProfiles(org.springframework.core.env.Profiles.of("dev", "test"))) { "Synthetic authentication requires an explicit dev or test profile; real-account production authentication is not implemented." }
        http.authorizeHttpRequests { requests ->
            if (environment.getProperty("portal.write-behind.enabled", Boolean::class.java, false)) {
                // Snapshot-based decisions require a frozen academic configuration for this bounded experiment.
                requests.requestMatchers("/api/v1/admin/**").denyAll()
            }
            requests.requestMatchers("/actuator/health", "/actuator/health/liveness", "/actuator/health/readiness",
                "/api/v1/auth/csrf", "/api/v1/auth/login").permitAll()
                .requestMatchers("/api/v1/me", "/api/v1/auth/me", "/api/v1/auth/logout").authenticated()
                .requestMatchers("/api/v1/admin/benchmark-authorize").hasRole("ADMIN")
                .requestMatchers("/api/v1/terms", "/api/v1/courses", "/api/v1/me/**", "/api/v1/sections/**", "/api/v1/enrollments/**", "/api/v1/courses/**").hasRole("STUDENT")
                .anyRequest().denyAll()
        }
        http.httpBasic { it.disable() }.requestCache { it.disable() }
        http.sessionManagement { it.sessionFixation { fixation -> fixation.changeSessionId() } }
        http.formLogin { login ->
            login.loginPage("/api/v1/auth/login").loginProcessingUrl("/api/v1/auth/login")
                .successHandler { request, response, authentication ->
                    request.session.setAttribute("authenticatedAt", System.currentTimeMillis())
                    response.contentType = MediaType.APPLICATION_JSON_VALUE
                    val actor = (authentication.principal as AccountPrincipal).actor
                    mapper.writeValue(response.outputStream, mapOf("principalId" to actor.principalId, "studentId" to actor.studentId, "role" to actor.role))
                }
                .failureHandler { _, response, _ -> problem(mapper, response, 401, "AUTHENTICATION_FAILED") }
        }
        http.logout { logout ->
            logout.logoutUrl("/api/v1/auth/logout").invalidateHttpSession(true).clearAuthentication(true)
                .deleteCookies("SESSION").logoutSuccessHandler { _, response, _ ->
                    response.contentType = MediaType.APPLICATION_JSON_VALUE
                    mapper.writeValue(response.outputStream, mapOf("code" to "LOGGED_OUT"))
                }
        }
        http.exceptionHandling { exceptions ->
            exceptions.authenticationEntryPoint { _, response, _ -> problem(mapper, response, 401, "AUTHENTICATION_REQUIRED") }
            exceptions.accessDeniedHandler { _, response, _ -> problem(mapper, response, 403, "FORBIDDEN") }
        }
        // Not a servlet bean: this guard runs exactly once, inside Spring Security after context loading.
        http.addFilterBefore(AccountGuard(accounts, mapper, metrics, environment.getProperty("portal.authentication.login-attempts-per-minute", Int::class.java, 60)), CsrfFilter::class.java)
        return http.build()
    }
}

private fun problem(mapper: JsonMapper, response: HttpServletResponse, status: Int, code: String) {
    if (status == 401 || status == 403) SessionStorageBoundary.recordSecurityDenial(HttpStatus.valueOf(status), code)
    response.status = status
    response.contentType = MediaType.APPLICATION_PROBLEM_JSON_VALUE
    val body = ProblemResponses.create(HttpStatus.valueOf(status), code, "The request is not permitted.")
    if (status >= 500 || status == 429) { body.setProperty("retryable", true); if (!response.containsHeader("Retry-After")) response.setHeader("Retry-After", "1") }
    mapper.writeValue(response.outputStream, body)
}

private class AccountGuard(private val accounts: Accounts, private val mapper: JsonMapper, private val metrics: org.example.sdu.registration.shared.observability.EnrollmentMetrics, private val loginLimit: Int) : OncePerRequestFilter() {
    private val loginSlots = Semaphore(8)
    private val minute = AtomicLong(System.currentTimeMillis() / 60_000)
    private val logins = AtomicInteger()
    override fun shouldNotFilter(request: HttpServletRequest): Boolean = !request.requestURI.startsWith("/api/")
    override fun doFilterInternal(request: HttpServletRequest, response: HttpServletResponse, chain: FilterChain) {
        val principal = SecurityContextHolder.getContext().authentication?.principal as? AccountPrincipal
        if (principal != null) {
            val authenticatedAt = request.getSession(false)?.getAttribute("authenticatedAt") as? Long ?: 0L
            val valid = try { metrics.stage("authentication") { accounts.valid(principal) } } catch (failure: org.springframework.dao.DataAccessException) {
                if (!isTransientDependencyFailure(failure)) throw failure
                problem(mapper, response, 503, "TEMPORARILY_UNAVAILABLE"); return
            }
            if (!valid || System.currentTimeMillis() - authenticatedAt >= 8 * 60 * 60 * 1000L) {
                SecurityContextHolder.clearContext()
                SessionStorageBoundary.recordSecurityDenial(HttpStatus.UNAUTHORIZED, "AUTHENTICATION_REQUIRED")
                request.getSession(false)?.invalidate()
                problem(mapper, response, 401, "AUTHENTICATION_REQUIRED"); return
            }
        }
        val login = request.method == "POST" && request.requestURI == "/api/v1/auth/login"
        if (!login) { chain.doFilter(request, response); return }
        val nowMinute = System.currentTimeMillis() / 60_000
        if (minute.getAndSet(nowMinute) != nowMinute) logins.set(0)
        if (logins.incrementAndGet() > loginLimit || !loginSlots.tryAcquire()) {
            response.setHeader("Retry-After", "60"); problem(mapper, response, 429, "RATE_LIMITED"); return
        }
        try {
            if ((request.getParameter("username")?.length ?: 0) !in 1..100 ||
                (request.getParameter("password")?.length ?: 0) !in 1..72 ||
                (request.getParameter("password")?.toByteArray(Charsets.UTF_8)?.size ?: 0) > 72) {
                problem(mapper, response, 401, "AUTHENTICATION_FAILED"); return
            }
            chain.doFilter(request, response)
        } finally { loginSlots.release() }
    }
}
