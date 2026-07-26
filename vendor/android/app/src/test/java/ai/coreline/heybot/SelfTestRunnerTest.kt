package ai.coreline.heybot

import kotlinx.coroutines.delay
import kotlinx.coroutines.async
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class SelfTestRunnerTest {
    @Test
    fun `production quick mode runs deterministic offline cases`() = runBlocking {
        val runner = SelfTestRunner.production(
            environment = mapOf("IRIS_CONVERSATION_PROXY_ENABLED" to "false")
        )

        val report = runner.run(SelfTestMode.QUICK)

        assertEquals(SelfTestStatus.PASS, report.status)
        assertTrue(report.cases.isNotEmpty())
        assertTrue(report.cases.none { it.status == SelfTestStatus.FAIL })
        assertTrue(report.render().contains("자체진단 빠른 PASS"))
    }

    @Test
    fun `a timed out case does not prevent later cases from running`() = runBlocking {
        var completed = false
        val runner = SelfTestRunner.forTesting(
            caseTimeoutMillis = 20L,
            cases = listOf(
                SelfTestCaseDefinition("slow", setOf(SelfTestMode.QUICK)) {
                    delay(200L)
                    SelfTestCaseResult("slow", SelfTestStatus.PASS, "UNEXPECTED", 0L)
                },
                SelfTestCaseDefinition("fast", setOf(SelfTestMode.QUICK)) {
                    completed = true
                    SelfTestCaseResult("fast", SelfTestStatus.PASS, "OK", 0L)
                }
            )
        )

        val report = runner.run(SelfTestMode.QUICK)

        assertEquals(SelfTestStatus.FAIL, report.status)
        assertEquals("CASE_TIMEOUT", report.cases.first { it.id == "slow" }.code)
        assertTrue(completed)
        assertEquals(SelfTestStatus.PASS, report.cases.first { it.id == "fast" }.status)
    }

    @Test
    fun `busy runner returns stable warning instead of overlapping runs`() = runBlocking {
        val runner = SelfTestRunner.forTesting(
            caseTimeoutMillis = 500L,
            cases = listOf(
                SelfTestCaseDefinition("slow", setOf(SelfTestMode.QUICK)) {
                    delay(100L)
                    SelfTestCaseResult("slow", SelfTestStatus.PASS, "OK", 0L)
                }
            )
        )

        val first = async { runner.run(SelfTestMode.QUICK) }
        delay(10L)
        val second = runner.run(SelfTestMode.QUICK)

        assertEquals(SelfTestStatus.WARN, second.status)
        assertEquals("SELF_TEST_BUSY", second.cases.single().code)
        assertEquals(SelfTestStatus.PASS, first.await().status)
    }

    @Test
    fun `a partial skip is visible as warning`() = runBlocking {
        val runner = SelfTestRunner.forTesting(
            cases = listOf(
                SelfTestCaseDefinition("pass", setOf(SelfTestMode.CANARY)) {
                    SelfTestCaseResult("pass", SelfTestStatus.PASS, "OK", 0L)
                },
                SelfTestCaseDefinition("canary", setOf(SelfTestMode.CANARY)) {
                    SelfTestCaseResult("canary", SelfTestStatus.SKIP, "CANARY_EXPLICIT_CONFIRMATION_REQUIRED", 0L)
                }
            )
        )

        assertEquals(SelfTestStatus.WARN, runner.run(SelfTestMode.CANARY).status)
    }
}
