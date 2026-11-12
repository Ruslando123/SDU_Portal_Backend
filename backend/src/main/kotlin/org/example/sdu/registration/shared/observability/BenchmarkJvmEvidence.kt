package org.example.sdu.registration.shared.observability

import com.sun.management.HotSpotDiagnosticMXBean
import com.zaxxer.hikari.HikariDataSource
import jakarta.annotation.PostConstruct
import jakarta.annotation.PreDestroy
import jakarta.servlet.FilterChain
import jakarta.servlet.http.HttpServletRequest
import jakarta.servlet.http.HttpServletResponse
import jdk.jfr.Recording
import jdk.jfr.Event
import jdk.jfr.Name
import org.apache.tomcat.util.threads.ThreadPoolExecutor
import org.springframework.beans.factory.annotation.Value
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.boot.tomcat.TomcatWebServer
import org.springframework.boot.web.server.context.WebServerInitializedEvent
import org.springframework.context.event.EventListener
import org.springframework.context.annotation.Profile
import org.springframework.core.Ordered
import org.springframework.core.annotation.Order
import org.springframework.stereotype.Component
import org.springframework.web.filter.OncePerRequestFilter
import java.lang.management.ManagementFactory
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.StandardOpenOption.APPEND
import java.nio.file.StandardOpenOption.CREATE
import java.time.Duration
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit.SECONDS
import java.util.concurrent.TimeUnit.MILLISECONDS
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicLong

@Component
@Profile("benchmark")
@ConditionalOnProperty("portal.benchmark.jvm-evidence", havingValue = "true")
@Order(Ordered.HIGHEST_PRECEDENCE + 20)
class BenchmarkJvmEvidence(
    @Value("\${portal.benchmark.jvm-evidence-directory:/tmp/sdu-cloud/jvm-evidence}") directory: String,
    @Value("\${portal.benchmark.saturation-evidence:false}") private val captureSaturation: Boolean,
    private val dataSource: HikariDataSource,
) : OncePerRequestFilter() {
    private val path = Path.of(directory)
    private val active = AtomicInteger()
    private val peak = AtomicInteger()
    private val virtual = AtomicLong()
    private val platform = AtomicLong()
    private val sampler = Executors.newSingleThreadScheduledExecutor { task ->
        Thread.ofPlatform().daemon().name("benchmark-jvm-evidence").unstarted(task)
    }
    private val recording = Recording()
    private var samples = 0
    @Volatile private var requestExecutor: ThreadPoolExecutor? = null
    private var saturationDumps = 0
    private var lastSaturationNanos: Long? = null

    @PostConstruct fun start() {
        Files.createDirectories(path)
        recording.setMaxAge(Duration.ofMinutes(16))
        recording.setMaxSize(24L * 1024 * 1024)
        recording.enable("jdk.VirtualThreadPinned").withThreshold(Duration.ofMillis(1)).withStackTrace()
        recording.enable("jdk.ExecutionSample").withPeriod(Duration.ofMillis(20))
        recording.enable("jdk.JavaMonitorEnter").withThreshold(Duration.ofMillis(20)).withStackTrace()
        recording.enable("jdk.GarbageCollection")
        recording.enable("jdk.CPULoad").withPeriod(Duration.ofSeconds(1))
        if (captureSaturation) {
            for (event in listOf("jdk.ThreadPark", "jdk.SocketRead", "jdk.SocketWrite")) {
                recording.enable(event).withThreshold(Duration.ofMillis(10)).withStackTrace()
            }
            for (event in listOf("jdk.GCHeapSummary", "jdk.G1HeapSummary", "jdk.GCCPUTime",
                "jdk.GCPhasePauseLevel1", "jdk.SafepointStateSynchronization")) {
                recording.enable(event)
            }
            recording.enable(PoolSaturation::class.java).withoutStackTrace()
            recording.enable(EnrollmentTransactionEvent::class.java).withThreshold(Duration.ofMillis(50)).withoutStackTrace()
            sampler.scheduleWithFixedDelay({
                try { captureIfSaturated() } catch (failure: Exception) { logger.warn("Pool saturation capture failed", failure) }
            }, 250, 250, MILLISECONDS)
        }
        recording.start()
        sampler.scheduleWithFixedDelay({
            try { sample() } catch (failure: Exception) { logger.warn("Benchmark JVM evidence failed", failure) }
        }, 1, 1, SECONDS)
    }

    @EventListener
    fun captureExecutor(event: WebServerInitializedEvent) {
        if (captureSaturation && event.applicationContext.serverNamespace == null) {
            val server = event.webServer as TomcatWebServer
            requestExecutor = server.tomcat.service.findConnectors().single().protocolHandler.executor as ThreadPoolExecutor
        }
    }

    private fun pools(): PoolSaturation? {
        val executor = requestExecutor ?: return null
        val pool = dataSource.hikariPoolMXBean ?: return null
        return PoolSaturation().apply {
            tomcatBusy = executor.activeCount
            tomcatMax = executor.maximumPoolSize
            hikariActive = pool.activeConnections
            hikariMax = dataSource.maximumPoolSize
            hikariPending = pool.threadsAwaitingConnection
        }
    }

    private fun captureIfSaturated() {
        val now = System.nanoTime()
        if (saturationDumps >= 8 || lastSaturationNanos?.let { now - it < SECONDS.toNanos(20) } == true) return
        val state = pools() ?: return
        if (state.tomcatBusy < state.tomcatMax || state.hikariActive < state.hikariMax || state.hikariPending == 0) return
        lastSaturationNanos = now
        val epochMs = System.currentTimeMillis()
        state.commit()
        val name = "threads-saturation-${saturationDumps++}.txt"
        dumpThreads(name)
        Files.writeString(path.resolve("saturation.tsv"), "$epochMs\t${state.counts()}\t$name\n", CREATE, APPEND)
    }

    private fun dumpThreads(name: String) {
        ManagementFactory.getPlatformMXBean(HotSpotDiagnosticMXBean::class.java)
            .dumpThreads(path.resolve(name).toString(), HotSpotDiagnosticMXBean.ThreadDumpFormat.TEXT_PLAIN)
    }

    private fun sample() {
        Files.writeString(path.resolve("requests.tsv"), "${System.currentTimeMillis()}\t${active.get()}\t${peak.get()}\t${virtual.get()}\t${platform.get()}\n", CREATE, APPEND)
        pools()?.let { Files.writeString(path.resolve("pools.tsv"), "${System.currentTimeMillis()}\t${it.counts()}\n", CREATE, APPEND) }
        if (samples % 30 == 0 && samples < 540) {
            dumpThreads("threads-$samples.txt")
        }
        samples++
        if (Files.exists(path.resolve("finish"))) {
            recording.stop()
            recording.dump(path.resolve("benchmark.jfr"))
            Files.createFile(path.resolve("complete"))
            sampler.shutdown()
        }
    }

    override fun shouldNotFilter(request: HttpServletRequest) = !request.requestURI.startsWith("/api/")

    override fun doFilterInternal(request: HttpServletRequest, response: HttpServletResponse, chain: FilterChain) {
        if (Thread.currentThread().isVirtual) virtual.incrementAndGet() else platform.incrementAndGet()
        peak.accumulateAndGet(active.incrementAndGet(), ::maxOf)
        try { chain.doFilter(request, response) } finally { active.decrementAndGet() }
    }

    @PreDestroy fun stop() {
        sampler.shutdownNow()
        sampler.awaitTermination(5, SECONDS)
        recording.close()
    }
}

@Name("sdu.PoolSaturation")
private class PoolSaturation : Event() {
    @JvmField var tomcatBusy = 0
    @JvmField var tomcatMax = 0
    @JvmField var hikariActive = 0
    @JvmField var hikariMax = 0
    @JvmField var hikariPending = 0

    fun counts() = "$tomcatBusy\t$tomcatMax\t$hikariActive\t$hikariMax\t$hikariPending"
}
