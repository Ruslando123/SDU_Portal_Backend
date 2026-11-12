package org.example.sdu.registration.enrollment.internal.memory

import com.rabbitmq.client.*
import java.nio.file.Files
import java.nio.file.Path
import java.security.KeyStore
import java.security.cert.CertificateFactory
import java.util.concurrent.atomic.AtomicBoolean
import javax.net.ssl.SSLContext
import javax.net.ssl.TrustManagerFactory

/** One owner, one serial publisher, one ordered projector. No automatic recovery with stale RAM. */
interface DecisionJournal : AutoCloseable {
    val healthy: Boolean
    fun publish(id: String, bytes: ByteArray)
    fun projectOne(project: (ByteArray) -> Unit): Boolean
}
fun interface DecisionJournalFactory { fun open(): DecisionJournal }

@org.springframework.stereotype.Component
@org.springframework.boot.autoconfigure.condition.ConditionalOnProperty(name = ["portal.write-behind.enabled"], havingValue = "true")
class RabbitJournalFactory(private val settings: MemorySettings) : DecisionJournalFactory {
    override fun open(): DecisionJournal = RabbitJournal(settings)
}

class RabbitJournal(private val settings: MemorySettings) : DecisionJournal {
    private val connection: Connection
    private val publisher: Channel
    private val reader: Channel
    private val returned = AtomicBoolean()
    override val healthy: Boolean get() = connection.isOpen && publisher.isOpen && reader.isOpen
    init {
        val factory = ConnectionFactory().apply {
            host = settings.host; port = settings.port; username = settings.username
            password = settings.password; virtualHost = settings.virtualHost
            isAutomaticRecoveryEnabled = false; isTopologyRecoveryEnabled = false
            connectionTimeout = 5_000; handshakeTimeout = 5_000; requestedHeartbeat = 5
            if (settings.tls) {
                val store = KeyStore.getInstance(KeyStore.getDefaultType()).apply { load(null, null) }
                Files.newInputStream(Path.of(settings.caFile)).use { stream ->
                    CertificateFactory.getInstance("X.509").generateCertificates(stream).forEachIndexed { i, cert -> store.setCertificateEntry("ca-$i", cert) }
                }
                val trust = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm()).apply { init(store) }
                useSslProtocol(SSLContext.getInstance("TLSv1.3").apply { init(null, trust.trustManagers, null) })
                enableHostnameVerification()
            }
        }
        connection = factory.newConnection("sdu-memory-decisions")
        try {
            publisher = connection.createChannel()
            // Exclusive owner queue prevents a second engine or projector on the same journal.
            publisher.queueDeclare(settings.queue + ".owner", false, true, true, emptyMap())
            publisher.queueDeclare(settings.queue, true, false, false, mapOf(
                "x-queue-type" to "quorum", "x-quorum-initial-group-size" to 1,
                "x-delivery-limit" to -1, "x-max-length" to settings.maxPending,
                "x-overflow" to "reject-publish"))
            publisher.confirmSelect()
            publisher.addReturnListener(ReturnListener { _, _, _, _, _, _ -> returned.set(true) })
            reader = connection.createChannel()
        } catch (failure: Throwable) { connection.abort(); throw failure }
    }
    @Synchronized override fun publish(id: String, bytes: ByteArray) {
        check(healthy)
        returned.set(false)
        publisher.basicPublish("", settings.queue, true, AMQP.BasicProperties.Builder()
            .deliveryMode(2).contentType("application/json").messageId(id).build(), bytes)
        publisher.waitForConfirmsOrDie(settings.confirmTimeoutMs)
        check(!returned.get()) { "Decision was not routed" }
    }
    /** ACK follows the caller's committed SQL transaction. A failure leaves the delivery recoverable. */
    override fun projectOne(project: (ByteArray) -> Unit): Boolean {
        val message = reader.basicGet(settings.queue, false) ?: return false
        check(message.props.deliveryMode == 2)
        try {
            project(message.body)
            reader.basicAck(message.envelope.deliveryTag, false)
        } catch (failure: Throwable) {
            if (reader.isOpen) reader.basicNack(message.envelope.deliveryTag, false, true)
            throw failure
        }
        return true
    }
    fun pending(): Long = reader.messageCount(settings.queue)
    override fun close() { connection.abort() }
}
