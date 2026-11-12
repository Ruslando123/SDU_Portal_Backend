package org.example.sdu.verification

import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import java.net.ServerSocket
import java.net.URI
import java.net.http.HttpClient
import java.net.http.HttpRequest
import java.net.http.HttpResponse
import java.util.Base64
import java.util.UUID

class MonitoringSecurityTest {
    @Test fun `separate management credential scrapes metrics without granting business authentication`() {
        val port=ServerSocket(0).use {it.localPort}
        val password=UUID.randomUUID().toString()+UUID.randomUUID()
        Phase5Harness("--portal.monitoring.enabled=true","--PORTAL_METRICS_PASSWORD=$password","--management.server.port=$port",
            "--management.endpoints.web.exposure.include=health,prometheus").use { app ->
            fun get(url:String,credentials:String?=null):HttpResponse<String> {
                val request=HttpRequest.newBuilder(URI.create(url)).GET()
                if(credentials!=null) request.header("Authorization","Basic "+Base64.getEncoder().encodeToString(credentials.toByteArray()))
                return HttpClient.newHttpClient().send(request.build(),HttpResponse.BodyHandlers.ofString())
            }
            val url="http://127.0.0.1:$port/actuator/prometheus"
            assertEquals(401,get(url).statusCode())
            assertEquals(401,get(url,"metrics:wrong").statusCode())
            val response=get(url,"metrics:$password")
            assertEquals(200,response.statusCode(),response.body())
            assertTrue(response.body().contains("jvm_memory_used_bytes"));assertTrue(response.body().contains("hikaricp_connections"))
            assertFalse(response.body().contains(password));assertFalse(response.headers().firstValue("Set-Cookie").isPresent)
            assertEquals(401,get(app.base+"/actuator/prometheus","metrics:$password").statusCode())
            assertEquals(401,get(app.base+"/api/v1/me","metrics:$password").statusCode())
        }
    }
}
