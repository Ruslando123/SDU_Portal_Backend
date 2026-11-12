package org.example.sdu.verification

import org.example.sdu.registration.shared.database.isTransientDependencyFailure
import org.example.sdu.registration.shared.errors.GlobalExceptionHandler
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import org.springframework.dao.DataAccessResourceFailureException
import org.springframework.transaction.CannotCreateTransactionException
import org.springframework.transaction.TransactionSystemException
import java.io.EOFException
import java.net.ConnectException
import java.sql.SQLException
import java.sql.SQLTransientConnectionException

class SessionFailureClassificationTest {
    @Test
    fun `HTTP advice classifies transaction acquisition exhaustion without concealing permanent errors`() {
        val advice = GlobalExceptionHandler()
        val transient = advice.unexpected(CannotCreateTransactionException("checkout", SQLTransientConnectionException()))
        assertEquals(503, transient.statusCode.value())
        assertEquals("1", transient.headers.getFirst("Retry-After"))
        assertEquals("TEMPORARILY_UNAVAILABLE", transient.body?.properties?.get("code"))
        assertEquals(true, transient.body?.properties?.get("retryable"))

        val permanent = advice.unexpected(CannotCreateTransactionException("checkout", SQLException("bad credentials", "28P01")))
        assertEquals(500, permanent.statusCode.value())
        assertEquals("INTERNAL_ERROR", permanent.body?.properties?.get("code"))
    }

    @Test
    fun `only evidence of connection loss or confirmed pool starvation is retryable`() {
        val refused = SQLException("connection refused", "08001", ConnectException())
        assertTrue(isTransientDependencyFailure(CannotCreateTransactionException("checkout", refused)))
        val proxyClosed = SQLException("startup socket closed", "08001", EOFException())
        assertTrue(isTransientDependencyFailure(CannotCreateTransactionException("checkout", proxyClosed)))
        assertFalse(isTransientDependencyFailure(DataAccessResourceFailureException("query", proxyClosed)))
        assertTrue(isTransientDependencyFailure(CannotCreateTransactionException("checkout", SQLTransientConnectionException())))
        assertFalse(isTransientDependencyFailure(CannotCreateTransactionException("checkout", SQLException("driver config", "08001"))))

        val statement = DataAccessResourceFailureException("session read", SQLException("connection lost", "08006"))
        val rollback = TransactionSystemException("rollback failed", SQLException("closed"))
        rollback.initApplicationException(statement)
        assertTrue(isTransientDependencyFailure(rollback))
    }

    @Test
    fun `permanent JDBC and application failures are not hidden by transaction failure`() {
        for (state in listOf("28P01", "28000", "42501", "42P01", "08004", "08P01")) {
            val checkout = SQLTransientConnectionException("checkout")
            checkout.setNextException(SQLException("permanent failure", state))
            assertFalse(isTransientDependencyFailure(CannotCreateTransactionException("checkout", checkout)), state)
        }
        val rollback = TransactionSystemException("rollback failed", SQLException("connection lost", "08006"))
        rollback.initApplicationException(IllegalStateException("programming error"))
        assertFalse(isTransientDependencyFailure(rollback))
    }
}
