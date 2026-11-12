package org.example.sdu.registration.shared.errors

class ApiFailure(val status: Int, val code: String, val retryable: Boolean = false, cause: Throwable? = null) : RuntimeException(code, cause)
