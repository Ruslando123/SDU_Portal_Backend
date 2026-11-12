package org.example.sdu.registration.shared.errors

import org.example.sdu.registration.shared.database.isTransientDependencyFailure
import org.slf4j.LoggerFactory
import org.springframework.http.HttpStatus
import org.springframework.http.ResponseEntity
import org.springframework.http.converter.HttpMessageNotReadableException
import org.springframework.transaction.CannotCreateTransactionException
import org.springframework.web.bind.annotation.ExceptionHandler
import org.springframework.web.bind.annotation.RestControllerAdvice

@RestControllerAdvice
class GlobalExceptionHandler {
    @ExceptionHandler(ApiFailure::class)
    fun expected(failure: ApiFailure): ResponseEntity<org.springframework.http.ProblemDetail> {
        val problem = ProblemResponses.create(HttpStatus.valueOf(failure.status), failure.code, "The request could not be completed.")
        problem.setProperty("retryable", failure.retryable)
        val response = ResponseEntity.status(failure.status)
        if (failure.retryable) response.header("Retry-After", "1")
        return response.body(problem)
    }

    @ExceptionHandler(IllegalArgumentException::class, HttpMessageNotReadableException::class,
        org.springframework.web.bind.MissingServletRequestParameterException::class,
        org.springframework.web.method.annotation.MethodArgumentTypeMismatchException::class)
    fun invalidRequest(): ResponseEntity<org.springframework.http.ProblemDetail> =
        ResponseEntity.badRequest().body(ProblemResponses.create(HttpStatus.BAD_REQUEST, "INVALID_REQUEST", "The request is invalid."))

    @ExceptionHandler(Exception::class)
    fun unexpected(exception: Exception): ResponseEntity<org.springframework.http.ProblemDetail> {
        if (exception is CannotCreateTransactionException && isTransientDependencyFailure(exception)) {
            logger.warn("Database transaction acquisition temporarily unavailable")
            return expected(ApiFailure(503, "TEMPORARILY_UNAVAILABLE", retryable = true))
        }
        if (exception is org.springframework.web.ErrorResponse && exception.statusCode.value() in 400..499) {
            val status = HttpStatus.valueOf(exception.statusCode.value())
            val code = when(status.value()) { 404 -> "RESOURCE_NOT_FOUND"; 405 -> "METHOD_NOT_ALLOWED"; 415 -> "UNSUPPORTED_MEDIA_TYPE"; else -> "INVALID_REQUEST" }
            return ResponseEntity.status(status).body(ProblemResponses.create(status, code, "The request is invalid."))
        }
        logger.error("Unhandled request failure: type={}", exception.javaClass.name)
        return ResponseEntity.internalServerError().body(
            ProblemResponses.create(HttpStatus.INTERNAL_SERVER_ERROR, "INTERNAL_ERROR", "An unexpected error occurred.")
        )
    }

    companion object { private val logger = LoggerFactory.getLogger(GlobalExceptionHandler::class.java) }
}
