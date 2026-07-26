package ai.coreline.heybot

import java.io.File

data class ConversationEngineModeSnapshot(
    val engine: ConversationEngine,
    val updatedAtMillis: Long?
)

/** Global provider mode. A missing/corrupt file is always the local GLM default. */
class ConversationEngineModeStore(
    private val file: File? = null,
    private val nowMillis: () -> Long = System::currentTimeMillis
) {
    private var current = load()

    @Synchronized
    fun snapshot(): ConversationEngineModeSnapshot = current

    @Synchronized
    fun set(engine: ConversationEngine): ConversationEngineModeSnapshot {
        val next = ConversationEngineModeSnapshot(engine, nowMillis())
        current = next
        persist(next)
        return next
    }

    @Synchronized
    fun reset(): ConversationEngineModeSnapshot = set(ConversationEngine.GLM)

    private fun load(): ConversationEngineModeSnapshot {
        val raw = runCatching { file?.takeIf { it.isFile }?.readText() }.getOrNull() ?: return default()
        val values = raw.lineSequence()
            .mapNotNull { line -> line.substringBefore('=').trim().takeIf(String::isNotBlank)?.let { it to line.substringAfter('=', "").trim() } }
            .toMap()
        if (values["schemaVersion"] != "1") return default()
        val engine = ConversationEngine.entries.firstOrNull { it.name == values["engine"] } ?: return default()
        return ConversationEngineModeSnapshot(engine, values["updatedAt"]?.toLongOrNull())
    }

    private fun persist(snapshot: ConversationEngineModeSnapshot) {
        val target = file ?: return
        runCatching {
            target.parentFile?.mkdirs()
            val temporary = File(target.parentFile, ".${target.name}.tmp")
            temporary.writeText(
                "schemaVersion=1\nengine=${snapshot.engine.name}\nupdatedAt=${snapshot.updatedAtMillis ?: 0L}\n"
            )
            temporary.renameTo(target)
        }
    }

    private fun default() = ConversationEngineModeSnapshot(ConversationEngine.GLM, null)

    companion object {
        fun inMemory(): ConversationEngineModeStore = ConversationEngineModeStore()
    }
}

class ConversationGatewayRouter(
    private val modeStore: ConversationEngineModeStore,
    private val glm: ConversationGateway,
    private val codex: ConversationGateway?,
    private val grok: ConversationGateway?
) : ConversationGateway {
    override suspend fun generate(request: GlmChatRequest): Result<GlmChatResponse> = when (modeStore.snapshot().engine) {
        ConversationEngine.GLM -> glm.generate(request)
        ConversationEngine.CODEX -> codex?.generate(request)
            ?: Result.failure(GlmFailure.Proxy("CODEX_PROXY_UNAVAILABLE"))
        ConversationEngine.GROK -> grok?.generate(request)
            ?: Result.failure(GlmFailure.Proxy("GROK_PROXY_UNAVAILABLE"))
    }

    fun isAvailable(engine: ConversationEngine): Boolean = when (engine) {
        ConversationEngine.GLM -> true
        ConversationEngine.CODEX -> codex != null
        ConversationEngine.GROK -> grok != null
    }
}
