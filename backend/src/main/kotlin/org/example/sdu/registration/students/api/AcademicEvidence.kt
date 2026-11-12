package org.example.sdu.registration.students.api

import java.math.BigDecimal
import java.util.UUID

data class Completion(val courseId: UUID, val passed: Boolean, val grade: BigDecimal?, val scheme: String?)
data class AcademicEvidence(val complete: Boolean, val active: Boolean, val completions: List<Completion>)
interface StudentEvidence { fun pinned(student: UUID, snapshot: UUID): AcademicEvidence? }
