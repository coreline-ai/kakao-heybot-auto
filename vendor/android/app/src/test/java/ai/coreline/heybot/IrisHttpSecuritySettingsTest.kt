package ai.coreline.heybot

import java.nio.file.Files
import kotlin.io.path.writeText
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class IrisHttpSecuritySettingsTest {
    @Test
    fun `http api is disabled by default and does not need a secret`() {
        assertTrue(IrisHttpSecuritySettings.load(emptyMap()) is IrisHttpSecuritySettingsLoadResult.Disabled)
        assertTrue(
            IrisHttpSecuritySettings.load(mapOf("IRIS_HTTP_API_ENABLED" to "false"))
                is IrisHttpSecuritySettingsLoadResult.Disabled
        )
    }

    @Test
    fun `enabled api requires an absolute readable bounded secret file`() {
        assertInvalid(mapOf("IRIS_HTTP_API_ENABLED" to "true"))
        assertInvalid(
            mapOf(
                "IRIS_HTTP_API_ENABLED" to "true",
                "IRIS_HTTP_ADMIN_SECRET_FILE" to "relative.token"
            )
        )

        val oversized = Files.createTempFile("iris-http-secret", ".txt")
        oversized.writeText("x".repeat(1_025))
        assertInvalid(enabledEnvironment(oversized.toString()))

        val target = Files.createTempFile("iris-http-secret", ".txt")
        target.writeText("s".repeat(48))
        val link = target.resolveSibling("${target.fileName}.link")
        Files.createSymbolicLink(link, target)
        assertInvalid(enabledEnvironment(link.toString()))
    }

    @Test
    fun `authenticator accepts only the exact bearer secret`() {
        val secret = Files.createTempFile("iris-http-secret", ".txt")
        secret.writeText("s".repeat(48))
        val result = IrisHttpSecuritySettings.load(enabledEnvironment(secret.toString()))
        val settings = (result as IrisHttpSecuritySettingsLoadResult.Ready).settings
        val auth = settings.authenticator()

        assertTrue(auth.isAuthorized("Bearer ${"s".repeat(48)}"))
        assertFalse(auth.isAuthorized(null))
        assertFalse(auth.isAuthorized("Basic ${"s".repeat(48)}"))
        assertFalse(auth.isAuthorized("Bearer ${"s".repeat(47)}x"))
        assertFalse(auth.isAuthorized("Bearer ${"x".repeat(2_000)}"))
    }

    @Test
    fun `invalid enabled flag fails closed`() {
        assertInvalid(mapOf("IRIS_HTTP_API_ENABLED" to "sometimes"))
    }

    private fun enabledEnvironment(secretFile: String) = mapOf(
        "IRIS_HTTP_API_ENABLED" to "true",
        "IRIS_HTTP_ADMIN_SECRET_FILE" to secretFile
    )

    private fun assertInvalid(environment: Map<String, String>) {
        assertTrue(IrisHttpSecuritySettings.load(environment) is IrisHttpSecuritySettingsLoadResult.Invalid)
    }
}
