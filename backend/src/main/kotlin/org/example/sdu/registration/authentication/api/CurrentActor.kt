package org.example.sdu.registration.authentication.api

import java.util.UUID

data class Actor(val principalId: UUID, val studentId: UUID?, val role: String, val authVersion: Long) : java.io.Serializable
interface CurrentActor {
    fun authenticated(): Actor
    fun student(): Actor
}
