package org.example.sdu.registration.shared.errors

import org.example.sdu.registration.shared.observability.CorrelationIdFilter
import org.slf4j.MDC
import org.springframework.http.HttpStatus
import org.springframework.http.ProblemDetail
import java.net.URI

object ProblemResponses {
    fun create(status: HttpStatus, code: String, detail: String): ProblemDetail =
        ProblemDetail.forStatusAndDetail(status, detail).apply {
            type = URI.create("urn:sdu:error:${code.lowercase().replace('_', '-')}")
            setProperty("code", code)
            setProperty("retryable", false)
            setProperty("correlationId", MDC.get(CorrelationIdFilter.MDC_KEY))
        }
}
