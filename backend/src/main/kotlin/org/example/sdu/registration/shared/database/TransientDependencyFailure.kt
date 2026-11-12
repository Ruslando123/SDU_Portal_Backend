package org.example.sdu.registration.shared.database

import org.springframework.dao.DataAccessException
import org.springframework.jdbc.CannotGetJdbcConnectionException
import org.springframework.transaction.CannotCreateTransactionException
import org.springframework.transaction.TransactionException
import org.springframework.transaction.TransactionSystemException
import java.io.EOFException
import java.net.ConnectException
import java.net.SocketTimeoutException
import java.sql.SQLException
import java.sql.SQLTransientConnectionException
import java.util.Collections
import java.util.IdentityHashMap

/** Recognize the acquisition and transport failures used by the pinned JDBC stack. */
internal fun isTransientDependencyFailure(failure: RuntimeException): Boolean {
    if (failure !is DataAccessException && failure !is TransactionException) return false
    val chain = exceptionCauses(failure)
    val sql = chain.filterIsInstance<SQLException>()
    val states = sql.mapNotNull { it.sqlState }.toSet()
    val acquiring = failure is CannotCreateTransactionException || failure is CannotGetJdbcConnectionException
    if (states.isEmpty()) {
        return acquiring &&
            failure.cause is SQLTransientConnectionException && failure.cause?.cause == null
    }
    if (chain.filterIsInstance<TransactionSystemException>().any { tx ->
            val original = tx.applicationException
            original != null && (original !is RuntimeException || !isTransientDependencyFailure(original))
        }) return false
    // A stopped proxy can close the socket during the driver's startup handshake.
    val transport = chain.any { it is ConnectException || it is SocketTimeoutException || (acquiring && it is EOFException) }
    return states.all { it in transientStates || (it == "08001" && transport) }
}

private val transientStates = setOf("08003", "08006", "08007", "57P01", "57P02", "57P03")

internal fun exceptionCauses(failure: Throwable): Set<Throwable> {
    val seen = Collections.newSetFromMap(IdentityHashMap<Throwable, Boolean>())
    val pending = ArrayDeque<Throwable>()
    pending.add(failure)
    while (pending.isNotEmpty()) {
        val next = pending.removeFirst()
        if (!seen.add(next)) continue
        next.cause?.let(pending::addLast)
        if (next is SQLException) next.nextException?.let(pending::addLast)
        // TransactionTemplate can replace a failed statement with a rollback exception.
        if (next is TransactionSystemException) next.applicationException?.let(pending::addLast)
    }
    return seen
}
