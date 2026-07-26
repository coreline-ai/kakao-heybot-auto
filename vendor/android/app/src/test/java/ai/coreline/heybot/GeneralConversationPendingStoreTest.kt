package ai.coreline.heybot

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class GeneralConversationPendingStoreTest {
    @Test
    fun `keeps at most two unfinished messages for one exact conversation key`() {
        val store = GeneralConversationPendingStore(maxMessagesPerConversation = 2, ttlMillis = 1_000L)
        val key = ConversationKey(chatId = 10L, userId = 20L)

        store.append(key, "첫 문장", 100L)
        store.append(key, "둘 문장", 200L)
        store.append(key, "셋 문장", 300L)

        assertEquals(listOf("둘 문장", "셋 문장"), store.messages(key, 300L))
    }

    @Test
    fun `separates users and rooms and removes expired messages`() {
        val store = GeneralConversationPendingStore(maxMessagesPerConversation = 2, ttlMillis = 1_000L)
        val first = ConversationKey(chatId = 10L, userId = 20L)
        val sameRoomOtherUser = ConversationKey(chatId = 10L, userId = 21L)
        val sameUserOtherRoom = ConversationKey(chatId = 11L, userId = 20L)

        store.append(first, "A", 100L)
        store.append(sameRoomOtherUser, "B", 100L)
        store.append(sameUserOtherRoom, "C", 100L)

        assertEquals(listOf("A"), store.messages(first, 1_100L))
        assertEquals(listOf("B"), store.messages(sameRoomOtherUser, 1_100L))
        assertEquals(listOf("C"), store.messages(sameUserOtherRoom, 1_100L))
        assertTrue(store.messages(first, 1_101L).isEmpty())
        assertTrue(store.messages(sameRoomOtherUser, 1_101L).isEmpty())
        assertTrue(store.messages(sameUserOtherRoom, 1_101L).isEmpty())
    }

    @Test
    fun `clears only the requested user or conversation`() {
        val store = GeneralConversationPendingStore()
        val first = ConversationKey(chatId = 10L, userId = 20L)
        val sameUserOtherRoom = ConversationKey(chatId = 11L, userId = 20L)
        val other = ConversationKey(chatId = 10L, userId = 21L)

        store.append(first, "A", 100L)
        store.append(sameUserOtherRoom, "B", 100L)
        store.append(other, "C", 100L)
        store.clear(first)

        assertTrue(store.messages(first, 100L).isEmpty())
        assertEquals(listOf("B"), store.messages(sameUserOtherRoom, 100L))
        store.clearUser(20L)

        assertTrue(store.messages(sameUserOtherRoom, 100L).isEmpty())
        assertEquals(listOf("C"), store.messages(other, 100L))
    }
}
