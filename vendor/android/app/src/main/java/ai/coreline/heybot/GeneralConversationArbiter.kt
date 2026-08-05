package ai.coreline.heybot

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

sealed interface GeneralConversationDecision {
    data class Reply(val text: String) : GeneralConversationDecision
    data object Wait : GeneralConversationDecision
    data object Ignore : GeneralConversationDecision
    data object Invalid : GeneralConversationDecision
}

/**
 * Converts one ordinary chat message into a strict participation decision.
 * The supplied history is already scoped to one exact (chatId, userId) key.
 * No chat text is logged or persisted by this class.
 */
class GeneralConversationArbiter(
    private val maxReplyChars: Int = MAX_REPLY_CHARS
) {
    fun buildRequest(
        settings: GlmSettings,
        message: String,
        history: List<ConversationTurn> = emptyList(),
        pendingMessages: List<String> = emptyList(),
        visionContext: VisionConversationContext? = null,
        audioContext: AudioConversationContext? = null
    ): GlmChatRequest = GlmChatRequest(
        model = settings.model,
        messages = buildList {
            add(GlmMessage(role = "system", content = HeybotPersona.generalConversationPrompt()))
            history.forEach { turn ->
                add(GlmMessage(role = "user", content = turn.userMessage))
                add(GlmMessage(role = "assistant", content = turn.assistantMessage))
            }
            visionContext?.let { add(VisionConversationContextRenderer.render(it)) }
            audioContext?.let { add(AudioConversationContextRenderer.render(it)) }
            pendingMessages.forEach { pending ->
                add(
                    GlmMessage(
                        role = "user",
                        content = "같은 사용자의 직전 미완성 발화입니다. 현재 발화와 함께 판단하세요.\n$pending"
                    )
                )
            }
            add(
                GlmMessage(
                    role = "user",
                    content = "현재 마지막 발화입니다.\n$message"
                )
            )
        },
        temperature = settings.temperature,
        // 128 tokens can cut a Korean JSON reply before its closing quote and
        // brace. The strict parser then sees INVALID and the user gets silence.
        // Reserve an independent budget for the complete decision envelope.
        maxTokens = maxOf(settings.maxTokens, GENERAL_CONVERSATION_MAX_TOKENS)
            .coerceAtMost(MAX_MODEL_TOKENS),
        timeoutMillis = settings.generalConversationTimeoutMillis,
        kind = GlmRequestKind.GENERAL_CONVERSATION
    )

    fun buildTruncationRetryRequest(request: GlmChatRequest): GlmChatRequest =
        request.copy(
            messages = request.messages.mapIndexed { index, message ->
                if (index == 0 && message.role == "system") {
                    message.copy(
                        content = message.content +
                            "\n이전 응답이 길이 제한으로 잘렸습니다. reply를 ${RETRY_REPLY_CHARS}자 이내로 줄여 " +
                            "완결된 JSON 객체를 처음부터 다시 반환하세요."
                    )
                } else {
                    message
                }
            },
            maxTokens = MAX_MODEL_TOKENS
        )

    fun parse(raw: String?): GeneralConversationDecision {
        val root = runCatching { Json.parseToJsonElement(raw?.trim().orEmpty()) }.getOrNull()
            ?: return GeneralConversationDecision.Invalid
        if (root !is JsonObject) return GeneralConversationDecision.Invalid

        val value = root.jsonObject
        if (value.keys != REQUIRED_FIELDS) return GeneralConversationDecision.Invalid
        val actionElement = value["action"]?.jsonPrimitive ?: return GeneralConversationDecision.Invalid
        val replyElement = value["reply"]?.jsonPrimitive ?: return GeneralConversationDecision.Invalid
        if (!actionElement.isString || !replyElement.isString) return GeneralConversationDecision.Invalid
        val action = actionElement.contentOrNull ?: return GeneralConversationDecision.Invalid
        val reply = replyElement.contentOrNull ?: return GeneralConversationDecision.Invalid

        return when (action) {
            "REPLY" -> reply
                .replace(Regex("\\s+"), " ")
                .trim()
                .takeIf { it.isNotBlank() && it.length <= maxReplyChars }
                ?.let(GeneralConversationDecision::Reply)
                ?: GeneralConversationDecision.Invalid
            "WAIT" -> if (reply.isEmpty()) GeneralConversationDecision.Wait else GeneralConversationDecision.Invalid
            "IGNORE" -> if (reply.isEmpty()) GeneralConversationDecision.Ignore else GeneralConversationDecision.Invalid
            else -> GeneralConversationDecision.Invalid
        }
    }

    private companion object {
        val REQUIRED_FIELDS = setOf("action", "reply")
        const val MAX_REPLY_CHARS = 300
        const val RETRY_REPLY_CHARS = 180
        const val GENERAL_CONVERSATION_MAX_TOKENS = 384
        const val MAX_MODEL_TOKENS = 512
    }
}
