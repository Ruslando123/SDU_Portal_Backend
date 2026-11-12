package org.example.sdu.registration.shared.database

import java.util.UUID

/** Optional authoritative overlay while PostgreSQL is an asynchronous projection. */
interface LiveSeatView {
    fun occupied(section: UUID): Int?
    fun availableOfferings(term: UUID): Set<UUID>
}
