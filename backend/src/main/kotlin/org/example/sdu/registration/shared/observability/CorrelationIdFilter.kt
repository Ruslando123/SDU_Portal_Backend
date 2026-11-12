package org.example.sdu.registration.shared.observability

import jakarta.servlet.FilterChain
import jakarta.servlet.http.HttpServletRequest
import jakarta.servlet.http.HttpServletResponse
import org.slf4j.MDC
import org.springframework.core.Ordered
import org.springframework.core.annotation.Order
import org.springframework.stereotype.Component
import org.springframework.web.filter.OncePerRequestFilter
import java.util.UUID

@Component
@Order(Ordered.HIGHEST_PRECEDENCE)
class CorrelationIdFilter : OncePerRequestFilter() {
    override fun doFilterInternal(request: HttpServletRequest, response: HttpServletResponse, chain: FilterChain) {
        val supplied = request.getHeader(HEADER)
        val correlationId = if (supplied != null && UUID_PATTERN.matches(supplied))
            UUID.fromString(supplied).toString() else UUID.randomUUID().toString()
        val previous = MDC.get(MDC_KEY)
        MDC.put(MDC_KEY, correlationId)
        response.setHeader(HEADER, correlationId)
        try { chain.doFilter(request, response) }
        finally {
            if (previous == null) MDC.remove(MDC_KEY) else MDC.put(MDC_KEY, previous)
        }
    }

    companion object {
        const val HEADER = "X-Correlation-ID"
        const val MDC_KEY = "correlationId"
        private val UUID_PATTERN = Regex("[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
    }
}
