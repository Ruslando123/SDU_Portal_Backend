package org.example.sdu.registration.authentication.internal.web

import io.lettuce.core.ClientOptions
import io.lettuce.core.SocketOptions
import io.lettuce.core.metrics.MicrometerCommandLatencyRecorder
import io.lettuce.core.metrics.MicrometerOptions
import io.lettuce.core.resource.ClientResources
import io.lettuce.core.resource.DefaultClientResources
import io.micrometer.core.instrument.MeterRegistry
import org.example.sdu.registration.authentication.api.Actor
import org.example.sdu.registration.authentication.internal.AccountPrincipal
import org.springframework.beans.factory.annotation.Value
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Configuration
import org.springframework.core.env.Environment
import org.springframework.dao.DataAccessException
import org.springframework.data.redis.connection.RedisPassword
import org.springframework.data.redis.connection.RedisStandaloneConfiguration
import org.springframework.data.redis.connection.lettuce.LettuceClientConfiguration
import org.springframework.data.redis.connection.lettuce.LettuceConnectionFactory
import org.springframework.data.redis.core.RedisTemplate
import org.springframework.data.redis.serializer.JdkSerializationRedisSerializer
import org.springframework.data.redis.serializer.RedisSerializer
import org.springframework.data.redis.serializer.SerializationException
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken
import org.springframework.security.core.authority.SimpleGrantedAuthority
import org.springframework.security.core.authority.FactorGrantedAuthority
import org.springframework.security.core.context.SecurityContextImpl
import org.springframework.security.web.authentication.WebAuthenticationDetails
import org.springframework.security.web.csrf.DefaultCsrfToken
import org.springframework.session.MapSession
import org.springframework.session.Session
import org.springframework.session.SessionRepository
import org.springframework.session.config.annotation.web.http.EnableSpringHttpSession
import org.springframework.session.data.redis.RedisSessionMapper
import org.springframework.session.data.redis.RedisSessionRepository
import java.io.ByteArrayInputStream
import java.io.ObjectInputFilter
import java.io.ObjectInputStream
import java.time.Duration
import java.time.Instant
import java.util.UUID
import java.util.concurrent.TimeUnit

@Configuration(proxyBeanMethods = false)
@ConditionalOnProperty(name = ["portal.session.store"], havingValue = "redis")
@EnableSpringHttpSession
class RedisSessionConfiguration {
    @Bean(destroyMethod = "shutdown")
    fun sessionRedisResources(registry: MeterRegistry): ClientResources =
        DefaultClientResources.builder().ioThreadPoolSize(2).computationThreadPoolSize(2)
            .commandLatencyRecorder(MicrometerCommandLatencyRecorder(registry,
                MicrometerOptions.builder().histogram(true).localDistinction(false).build()))
            .build()

    @Bean
    fun sessionRedisConnectionFactory(resources: ClientResources, environment: Environment): LettuceConnectionFactory {
        val host = environment.getProperty("portal.session.redis.host", "127.0.0.1")
        val port = environment.getProperty("portal.session.redis.port", Int::class.java, 6379)
        val tls = environment.getProperty("portal.session.redis.tls", Boolean::class.java, false)
        require(tls || host == "127.0.0.1" || host == "::1") { "Redis requires verified TLS outside loopback" }
        val endpoint = RedisStandaloneConfiguration(host, port).apply {
            username = environment.getProperty("portal.session.redis.username", "sdu-session")
            password = RedisPassword.of(environment.getRequiredProperty("portal.session.redis.password").also {
                require(it.isNotBlank()) { "Redis session credential is required" }
            })
        }
        val timeout = Duration.ofMillis(750)
        val options = ClientOptions.builder()
            .socketOptions(SocketOptions.builder().connectTimeout(timeout).build())
            .disconnectedBehavior(ClientOptions.DisconnectedBehavior.REJECT_COMMANDS).requestQueueSize(1024).build()
        val client = LettuceClientConfiguration.builder().clientResources(resources).clientOptions(options)
            .commandTimeout(timeout).shutdownTimeout(Duration.ofMillis(200))
        if (tls) client.useSsl() // Lettuce defaults to FULL chain and hostname verification.
        return LettuceConnectionFactory(endpoint, client.build())
    }

    @Bean
    fun redisSessionRepository(factory: LettuceConnectionFactory, registry: MeterRegistry,
        @Value("\${spring.session.timeout:30m}") timeout: Duration,
        @Value("\${portal.session.redis.namespace:sdu:http-session:v1}") namespace: String,
    ): SessionRepository<out Session> {
        require(!timeout.isZero && !timeout.isNegative && timeout <= Duration.ofMinutes(30))
        require(namespace.matches(Regex("sdu:http-session:[a-zA-Z0-9_-]{1,40}")))
        val serializer = JdkSerializationRedisSerializer()
        val template = RedisTemplate<String, Any>().apply {
            setConnectionFactory(factory)
            keySerializer = RedisSerializer.string()
            hashKeySerializer = RedisSerializer.string()
            hashValueSerializer = JdkSerializationRedisSerializer(
                { value -> serializer.serialize(value) },
                { bytes ->
                    if (bytes.size > 32768) throw SerializationException("Session attribute exceeds its size limit")
                    ObjectInputStream(ByteArrayInputStream(bytes)).use { input ->
                        input.setObjectInputFilter(sessionInputFilter)
                        input.readObject()
                    }
                },
            )
            afterPropertiesSet()
        }
        val repository = RedisSessionRepository(template).apply {
            setRedisKeyNamespace(namespace)
            setDefaultMaxInactiveInterval(timeout)
            val mapper = RedisSessionMapper()
            // A request racing logout can leave metadata without a creation time. Treat it as a miss.
            setRedisSessionMapper { id, attributes ->
                if (attributes.containsKey("creationTime")) mapper.apply(id, attributes) else MapSession(id).apply { lastAccessedTime = Instant.EPOCH }
            }
        }
        return measure(repository, registry)
    }

    private fun <S : Session> measure(repository: SessionRepository<S>, registry: MeterRegistry): SessionRepository<S> =
        object : SessionRepository<S> {
            override fun createSession(): S = repository.createSession()
            override fun findById(id: String): S? = timed("read") { repository.findById(id) }
            override fun save(session: S) = timed("write") { repository.save(session) }
            override fun deleteById(id: String) = timed("delete") { repository.deleteById(id) }

            private fun <T> timed(operation: String, work: () -> T): T {
                val started = System.nanoTime()
                try {
                    return work()
                } catch (failure: RuntimeException) {
                    if (failure !is DataAccessException && failure !is SerializationException) throw failure
                    registry.counter("session.redis.errors", "operation", operation).increment()
                    throw SessionStorageBoundary.storageUnavailable(failure)
                } finally {
                    registry.timer("session.redis", "operation", operation).record(System.nanoTime() - started, TimeUnit.NANOSECONDS)
                }
            }
        }

    companion object {
        private val allowed = setOf(
            Long::class.javaObjectType, Int::class.javaObjectType, Number::class.java,
            String::class.java, UUID::class.java, Any::class.java,
            Actor::class.java, AccountPrincipal::class.java, DefaultCsrfToken::class.java,
            SecurityContextImpl::class.java, UsernamePasswordAuthenticationToken::class.java,
            WebAuthenticationDetails::class.java, SimpleGrantedAuthority::class.java, FactorGrantedAuthority::class.java,
            Instant::class.java,
        ).map { it.name }.toSet() + setOf(
            "org.springframework.security.authentication.AbstractAuthenticationToken",
            "java.util.ArrayList", "java.util.Collections\$UnmodifiableCollection", "java.util.Collections\$UnmodifiableList",
            "java.util.Collections\$UnmodifiableRandomAccessList", "java.time.Ser",
        )

        private val sessionInputFilter = ObjectInputFilter { info ->
            val type = info.serialClass()?.let { if (it.isArray) it.componentType else it }
            when {
                info.depth() > 24 || info.references() > 256 || info.streamBytes() > 32768 ||
                    info.arrayLength() > 256 -> ObjectInputFilter.Status.REJECTED
                type == null || type.isPrimitive || type.name in allowed -> ObjectInputFilter.Status.UNDECIDED
                else -> ObjectInputFilter.Status.REJECTED
            }
        }
    }
}
