package ai.coreline.heybot

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

class AdminAuthorizerTest {
    @Test
    fun `loads only positive exact numeric user IDs`() {
        val file = File.createTempFile("iris-admins", ".txt").apply {
            writeText(
                """
                # root operators
                123
                123
                -1
                invalid
                456 # secondary
                """.trimIndent()
            )
            deleteOnExit()
        }

        val authorizer = AdminAuthorizer.fromFile(file) {}

        assertEquals(2, authorizer.adminCount)
        assertTrue(authorizer.isAdmin(123L))
        assertTrue(authorizer.isAdmin(456L))
        assertFalse(authorizer.isAdmin(124L))
    }

    @Test
    fun `missing file disables admin commands`() {
        val authorizer = AdminAuthorizer.fromFile(File("/missing/iris-admins")) {}

        assertEquals(0, authorizer.adminCount)
        assertFalse(authorizer.isAdmin(1L))
    }
}
