package org.example.sdu.registration.enrollment.internal

import org.example.sdu.registration.enrollment.api.*
import org.springframework.http.MediaType
import org.springframework.http.ResponseEntity
import org.springframework.web.bind.annotation.*
import java.util.UUID

data class EnrollRequest(val sectionId: UUID)
@RestController
@RequestMapping("/api/v1")
class EnrollmentController(private val commands: EnrollmentCommands, private val queries: OwnEnrollments) {
    @PostMapping("/sections/{id}/enrollments")
    fun enroll(@PathVariable id: UUID, @RequestHeader("Idempotency-Key", required = false) key: String?) = response(commands.enroll(id, key))
    @PostMapping("/me/enrollments")
    fun legacyEnroll(@RequestBody request: EnrollRequest, @RequestHeader("Idempotency-Key", required = false) key: String?) = response(commands.enroll(request.sectionId, key))
    @DeleteMapping("/enrollments/{id}", "/me/enrollments/{id}")
    fun withdraw(@PathVariable id: UUID, @RequestHeader("Idempotency-Key", required = false) key: String?) = response(commands.withdraw(id, key))
    @GetMapping("/me/enrollments")
    fun list(@RequestParam termId: UUID, @RequestParam(defaultValue = "100") limit: Int, @RequestParam(defaultValue = "0") offset: Int) = queries.list(termId, limit, offset)
    private fun response(result: CommittedOutcome): ResponseEntity<String> = ResponseEntity.status(result.status)
        .contentType(MediaType.APPLICATION_JSON).header("Idempotency-Replayed", result.replayed.toString()).body(result.body)
}
