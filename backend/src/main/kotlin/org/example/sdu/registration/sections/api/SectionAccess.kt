package org.example.sdu.registration.sections.api

import java.time.Instant
import java.util.UUID

data class Section(val id: UUID, val offering: UUID, val term: UUID, val published: Boolean, val capacity: Int, val count: Int)
data class Meeting(val id: UUID, val section: UUID, val startsAt: Instant, val endsAt: Instant, val required: Boolean, val kind: String, val venue: String?)
interface SectionFacts {
    fun locate(id: UUID): Section?
    fun meetings(ids: Set<UUID>): List<Meeting>
    fun published(offering: UUID, term: UUID, limit: Int, offset: Int): List<Section>
}
/** Enrollment's allocation/release port. Callers must already own gate and student locks. */
interface SectionAllocation {
    fun lock(id: UUID): Section?
    fun allocate(id: UUID): Boolean
    fun release(id: UUID)
}
