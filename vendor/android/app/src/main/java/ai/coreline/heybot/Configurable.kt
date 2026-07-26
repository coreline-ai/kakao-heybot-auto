package ai.coreline.heybot

import kotlinx.serialization.SerializationException
import kotlinx.serialization.json.Json
import java.io.File
import java.io.IOException
import ai.coreline.heybot.model.ConfigValues


class Configurable {
    companion object {
        private val CONFIG_FILE_PATH: String by lazy {
            System.getenv("IRIS_CONFIG_PATH") ?: "/data/local/private/iris-config.json"
        }
        private var configValues: ConfigValues = ConfigValues()

        private val json = Json {
            encodeDefaults = true
        }

        init {
            loadConfig()
        }

        private fun loadConfig() {
            val configFile = File(CONFIG_FILE_PATH)
            if (!configFile.exists()) {
                println("Iris config missing; creating defaults")
                saveConfig()
                return
            }

            try {
                val jsonString = configFile.readText()
                configValues = json.decodeFromString(ConfigValues.serializer(), jsonString)
            } catch (e: IOException) {
                System.err.println("Iris config read failed; creating defaults")
                saveConfig()
            } catch (e: SerializationException) {
                System.err.println("Iris config parse failed; creating defaults")
                saveConfig()
            }
        }

        private fun saveConfig() {
            try {
                val jsonString = json.encodeToString(ConfigValues.serializer(), configValues)
                File(CONFIG_FILE_PATH).writeText(jsonString)
            } catch (e: IOException) {
                System.err.println("Iris config write failed")
            } catch (e: SerializationException) {
                System.err.println("Iris config serialization failed")
            }
        }

        var botId: Long
            get() = configValues.botId
            set(value) {
                configValues.botId = value
                saveConfig()
                println("Bot ID updated")
            }

        var botName: String
            get() = configValues.botName
            set(value) {
                configValues.botName = value
                saveConfig()
                println("Bot name updated")
            }

        var botSocketPort: Int
            get() = configValues.botHttpPort
            set(value) {
                configValues.botHttpPort = value
                saveConfig()
                println("Bot HTTP port updated")
            }

        var webServerEndpoint: String
            get() = configValues.webServerEndpoint
            set(value) {
                configValues.webServerEndpoint = value
                saveConfig()
                println("Webhook endpoint updated")
            }

        var dbPollingRate: Long
            get() = configValues.dbPollingRate
            set(value) {
                configValues.dbPollingRate = value
                saveConfig()
                println("DB polling rate updated")
            }

        var messageSendRate: Long
            get() = configValues.messageSendRate
            set(value) {
                configValues.messageSendRate = value
                saveConfig()
                println("Message send rate updated")
                Replier.restartMessageSender()
            }
    }
}
