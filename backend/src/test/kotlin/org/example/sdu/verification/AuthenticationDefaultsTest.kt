package org.example.sdu.verification

import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import java.net.URI
import java.net.http.HttpClient
import java.net.http.HttpRequest
import java.net.http.HttpResponse

class AuthenticationDefaultsTest {
    @Test fun `session cookie is Secure HttpOnly SameSite by default before local override`() {
        Phase5Harness().use { app ->
            val request=HttpRequest.newBuilder(URI.create(app.base+"/api/v1/auth/csrf")).GET().build()
            val response=HttpClient.newHttpClient().send(request,HttpResponse.BodyHandlers.ofString())
            assertEquals(200,response.statusCode())
            val cookie=response.headers().allValues("Set-Cookie").joinToString()
            assertTrue(cookie.contains("Secure"));assertTrue(cookie.contains("HttpOnly"));assertTrue(cookie.contains("SameSite=Lax"))
        }
    }
}
