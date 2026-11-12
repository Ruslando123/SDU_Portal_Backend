package org.example.sdu.registration.authentication.internal.web

import org.example.sdu.registration.authentication.api.CurrentActor
import org.springframework.security.web.csrf.CsrfToken
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RestController

@RestController
class AuthenticationController(private val current: CurrentActor) {
    @GetMapping("/api/v1/auth/csrf")
    fun csrf(token: CsrfToken) = mapOf("token" to token.token, "headerName" to token.headerName, "parameterName" to token.parameterName)
    @PostMapping("/api/v1/admin/benchmark-authorize")
    fun benchmarkAuthorization() = mapOf("authorized" to true)

    @GetMapping("/api/v1/me", "/api/v1/auth/me")
    fun me() = current.authenticated().let { mapOf("principalId" to it.principalId, "studentId" to it.studentId, "role" to it.role) }
}
