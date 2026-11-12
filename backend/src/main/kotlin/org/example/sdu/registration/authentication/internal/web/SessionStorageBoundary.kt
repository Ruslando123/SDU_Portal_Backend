package org.example.sdu.registration.authentication.internal.web

import io.micrometer.core.instrument.MeterRegistry
import jakarta.servlet.DispatcherType
import jakarta.servlet.Filter
import jakarta.servlet.ServletException
import jakarta.servlet.http.HttpServletRequest
import jakarta.servlet.http.HttpServletResponse
import org.example.sdu.registration.shared.database.exceptionCauses
import org.example.sdu.registration.shared.database.isTransientDependencyFailure
import org.example.sdu.registration.shared.errors.ProblemResponses
import org.example.sdu.registration.shared.observability.CorrelationIdFilter
import org.slf4j.LoggerFactory
import org.springframework.beans.factory.annotation.Value
import org.springframework.boot.web.servlet.FilterRegistrationBean
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Configuration
import org.springframework.core.Ordered
import org.springframework.http.HttpStatus
import org.springframework.http.MediaType
import org.springframework.jdbc.datasource.DataSourceUtils
import org.springframework.transaction.PlatformTransactionManager
import org.springframework.transaction.TransactionDefinition
import org.springframework.transaction.TransactionStatus
import org.springframework.transaction.support.TransactionCallback
import org.springframework.transaction.support.TransactionOperations
import org.springframework.transaction.support.TransactionTemplate
import org.springframework.web.servlet.HandlerExceptionResolver
import org.springframework.web.servlet.ModelAndView
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer
import tools.jackson.databind.json.JsonMapper
import java.util.concurrent.TimeUnit
import java.util.function.Consumer
import javax.sql.DataSource

@Configuration(proxyBeanMethods = false)
class SessionStorageBoundary(@Value("\${portal.session.store:jdbc}") store: String) : WebMvcConfigurer {
    init { require(store == "jdbc" || store == "redis") { "Unknown HTTP session storage mode" } }

    // JdbcIndexedSessionRepository 4.1.1 uses execute for reads and executeWithoutResult for writes.
    @Bean
    @ConditionalOnProperty(name = ["portal.session.store"], havingValue = "jdbc", matchIfMissing = true)
    fun springSessionTransactionOperations(
        manager: PlatformTransactionManager, dataSource: DataSource, registry: MeterRegistry,
        @Value("\${portal.session.autocommit-reads:false}") autocommitReads: Boolean,
    ): TransactionOperations {
        val write = TransactionTemplate(manager).apply {
            propagationBehavior = TransactionDefinition.PROPAGATION_REQUIRES_NEW
        }
        // One SELECT per lookup. Suspend an ambient transaction so its isolation and connection stay independent.
        val read = if (autocommitReads) TransactionTemplate(manager).apply {
            propagationBehavior = TransactionDefinition.PROPAGATION_NOT_SUPPORTED
        } else write
        return object : TransactionOperations {
            override fun <T> execute(action: TransactionCallback<T>): T = run("read", read, action)

            override fun executeWithoutResult(action: Consumer<TransactionStatus>) {
                run("write", write) { status -> action.accept(status) }
            }

            private fun <T> run(operation: String, template: TransactionTemplate, action: TransactionCallback<T>): T {
                val request = current.get()
                request?.failure?.let { throw it }
                val started = System.nanoTime()
                var acquired = 0L
                try {
                    return template.execute { status ->
                        if (operation == "read" && autocommitReads) {
                            // Synchronization reuses this connection for JdbcTemplate and releases it on return.
                            val connection = DataSourceUtils.getConnection(dataSource)
                            acquired = System.nanoTime()
                            try { action.doInTransaction(status) }
                            finally { DataSourceUtils.releaseConnection(connection, dataSource) }
                        } else {
                            acquired = System.nanoTime()
                            action.doInTransaction(status)
                        }
                    }
                } catch (failure: RuntimeException) {
                    if (request == null || !isTransientDependencyFailure(failure)) throw failure
                    throw storageUnavailable(failure)
                } finally {
                    val finished = System.nanoTime()
                    if (acquired != 0L) {
                        record("acquire", acquired - started, operation)
                        record("hold", finished - acquired, operation)
                    }
                    record("total", finished - started, operation)
                }
            }

            private fun record(stage: String, nanos: Long, operation: String) {
                try { registry.timer("session.jdbc", "operation", operation, "stage", stage).record(nanos, TimeUnit.NANOSECONDS) }
                catch (_: Exception) { /* Metrics do not decide a session operation. */ }
            }
        }
    }

    @Bean
    fun sessionStorageFailureFilter(mapper: JsonMapper): FilterRegistrationBean<Filter> {
        val filter = Filter { rawRequest, rawResponse, chain ->
            val request = rawRequest as HttpServletRequest
            val response = rawResponse as HttpServletResponse
            val state = (request.getAttribute(STATE) as? RequestState) ?: RequestState().also {
                request.setAttribute(STATE, it)
            }
            // A committed failure may cause a container ERROR dispatch. Do not re-enter session/security.
            if (request.dispatcherType == DispatcherType.ERROR && state.failure != null) return@Filter
            val previous = current.get()
            current.set(state)
            try {
                try {
                    chain.doFilter(request, response)
                } catch (failure: Exception) {
                    val unavailable = state.failure
                    if (unavailable == null || unavailable !in exceptionCauses(failure)) throw failure
                }
                if (state.failure != null) respond(response, mapper, state.denial)
            } finally {
                if (previous == null) current.remove() else current.set(previous)
            }
        }
        return FilterRegistrationBean(filter).apply {
            order = Ordered.HIGHEST_PRECEDENCE + 1 // After correlation, before Boot's SessionRepositoryFilter.
            setDispatcherTypes(DispatcherType.REQUEST, DispatcherType.ASYNC, DispatcherType.ERROR)
        }
    }

    override fun extendHandlerExceptionResolvers(resolvers: MutableList<HandlerExceptionResolver>) {
        // A session commit can be triggered by a message converter before the servlet chain unwinds.
        resolvers.add(0, HandlerExceptionResolver { _, _, _, failure ->
            if (exceptionCauses(failure).any { it is SessionStorageUnavailable }) ModelAndView() else null
        })
    }

    private fun respond(response: HttpServletResponse, mapper: JsonMapper, denial: SecurityDenial?) {
        if (response.isCommitted) {
            logger.warn("Session storage unavailable after HTTP commitment")
            // Let the container terminate the failed exchange without exposing the storage exception.
            throw ServletException("Session storage unavailable after HTTP commitment")
        }
        val status = denial?.status ?: HttpStatus.SERVICE_UNAVAILABLE
        val code = denial?.code ?: "TEMPORARILY_UNAVAILABLE"
        val correlation = response.getHeader(CorrelationIdFilter.HEADER)
        response.reset()
        response.status = status.value()
        response.contentType = MediaType.APPLICATION_PROBLEM_JSON_VALUE
        response.setHeader("Cache-Control", "no-store")
        response.setHeader("X-Content-Type-Options", "nosniff")
        if (correlation != null) response.setHeader(CorrelationIdFilter.HEADER, correlation)
        val retryable = status == HttpStatus.SERVICE_UNAVAILABLE
        if (retryable) response.setHeader("Retry-After", "1")
        val detail = if (retryable) "Session storage is temporarily unavailable." else "The request is not permitted."
        val problem = ProblemResponses.create(status, code, detail)
        problem.setProperty("retryable", retryable)
        problem.setProperty("correlationId", correlation)
        mapper.writeValue(response.outputStream, problem)
    }

    private class SessionStorageUnavailable(cause: RuntimeException) : RuntimeException("Session storage unavailable", cause)
    private data class SecurityDenial(val status: HttpStatus, val code: String)
    private class RequestState(var failure: SessionStorageUnavailable? = null, var denial: SecurityDenial? = null)

    companion object {
        private val logger = LoggerFactory.getLogger(SessionStorageBoundary::class.java)
        private val current = ThreadLocal<RequestState>()
        private val STATE = SessionStorageBoundary::class.java.name + ".state"

        internal fun recordSecurityDenial(status: HttpStatus, code: String) {
            current.get()?.denial = SecurityDenial(status, code)
        }

        internal fun storageUnavailable(failure: RuntimeException): RuntimeException =
            SessionStorageUnavailable(failure).also { current.get()?.failure = it }
    }
}
