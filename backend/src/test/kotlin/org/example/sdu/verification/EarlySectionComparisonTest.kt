package org.example.sdu.verification

import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class EarlySectionComparisonTest {
    @Test fun `isolated early lock comparison keeps eligibility duplicate and withdrawal behavior`() {
        Phase5Harness("--spring.profiles.active=test,benchmark","--portal.monitoring.enabled=false",
            "--management.server.port=-1","--enrollment.benchmark-early-section-locking=true").use { app ->
            val fixture=Fixture().populate(app.db)
            val result=app.enroll(fixture)
            assertEquals("ENROLLED",result.code)
            assertEquals("ALREADY_ENROLLED",app.enroll(fixture).code)
            assertEquals("WITHDRAWN",app.withdraw(fixture,app.id(result)).code)
            Phase5InvariantChecks.verify(app.db)
        }
    }
}
