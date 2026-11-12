package org.example.sdu.registration.enrollment.internal.memory

import io.micrometer.core.instrument.MeterRegistry
import jakarta.servlet.FilterChain
import jakarta.servlet.http.HttpServletRequest
import jakarta.servlet.http.HttpServletResponse
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.core.Ordered
import org.springframework.core.annotation.Order
import org.springframework.stereotype.Component
import org.springframework.web.filter.OncePerRequestFilter
import java.util.concurrent.atomic.AtomicInteger

/** Private metrics only. Includes authentication/session processing; excludes the metrics scrape itself. */
@Component
@Order(Ordered.HIGHEST_PRECEDENCE + 5)
@ConditionalOnProperty(name=["portal.write-behind.enabled"], havingValue="true")
class MemoryHttpActivity(metrics: MeterRegistry) : OncePerRequestFilter() {
    private val active = AtomicInteger()
    init { metrics.gauge("portal.memory.http.active",active) }
    override fun shouldNotFilter(request: HttpServletRequest) = !request.requestURI.startsWith("/api/v1/")
    override fun doFilterInternal(request: HttpServletRequest, response: HttpServletResponse, chain: FilterChain) {
        active.incrementAndGet()
        try { chain.doFilter(request,response) } finally { active.decrementAndGet() }
    }
}
