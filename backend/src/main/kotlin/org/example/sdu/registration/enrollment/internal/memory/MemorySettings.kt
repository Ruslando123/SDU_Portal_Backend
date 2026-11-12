package org.example.sdu.registration.enrollment.internal.memory

import jakarta.annotation.PostConstruct
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.boot.context.properties.ConfigurationProperties
import org.springframework.core.env.Environment
import org.springframework.stereotype.Component

@Component
@ConditionalOnProperty(name = ["portal.write-behind.enabled"], havingValue = "true")
@ConfigurationProperties("portal.write-behind")
class MemorySettings(private val environment: Environment) {
    var host = "localhost"
    var port = 5671
    var username = "portal"
    var password = ""
    var virtualHost = "/portal"
    var queue = "portal.decisions.v1"
    var caFile = ""
    var tls = true
    var syntheticOnly = false
    var maxPending = 10_000
    var maxDecisions = 100_000
    var maxRuntimeSeconds = 900L
    var confirmTimeoutMs = 3_000L
    var projectorMode = "jdbc"
    var autoProject = true
    var eagerStart = true
    @PostConstruct fun validate() {
        require(environment.acceptsProfiles(org.springframework.core.env.Profiles.of("benchmark", "test"))) { "Memory decisions are an isolated experiment" }
        require(syntheticOnly) { "An isolated synthetic database is required" }
        require(password.isNotBlank() && queue.matches(Regex("[a-zA-Z0-9._-]{1,100}")))
        require(projectorMode in setOf("jdbc", "sql"))
        require(maxPending in 1..100_000 && maxDecisions in maxPending..1_000_000)
        require(maxRuntimeSeconds in 30..1_800 && confirmTimeoutMs in 100..5_000)
        require(tls && caFile.isNotBlank() || environment.acceptsProfiles(org.springframework.core.env.Profiles.of("test")))
        require(environment.getProperty("SDU_CATALOG_REPLICA_URL").isNullOrBlank())
        require(eagerStart || environment.acceptsProfiles(org.springframework.core.env.Profiles.of("test")))
        require(autoProject || environment.acceptsProfiles(org.springframework.core.env.Profiles.of("test")))
    }
}
