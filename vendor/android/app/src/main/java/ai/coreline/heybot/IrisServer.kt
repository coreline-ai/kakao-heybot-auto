package ai.coreline.heybot

import io.ktor.http.ContentType
import io.ktor.http.HttpHeaders
import io.ktor.http.HttpStatusCode
import io.ktor.serialization.kotlinx.KotlinxWebsocketSerializationConverter
import io.ktor.serialization.kotlinx.json.json
import io.ktor.server.application.ApplicationCallPipeline
import io.ktor.server.application.call
import io.ktor.server.application.install
import io.ktor.server.engine.embeddedServer
import io.ktor.server.netty.Netty
import io.ktor.server.plugins.contentnegotiation.ContentNegotiation
import io.ktor.server.plugins.statuspages.StatusPages
import io.ktor.server.request.path
import io.ktor.server.request.receive
import io.ktor.server.response.respond
import io.ktor.server.response.respondText
import io.ktor.server.routing.get
import io.ktor.server.routing.intercept
import io.ktor.server.routing.post
import io.ktor.server.routing.route
import io.ktor.server.routing.routing
import io.ktor.server.websocket.WebSockets
import io.ktor.server.websocket.webSocket
import io.ktor.websocket.send
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonPrimitive
import ai.coreline.heybot.model.ApiResponse
import ai.coreline.heybot.model.CommonErrorResponse
import ai.coreline.heybot.model.ConfigRequest
import ai.coreline.heybot.model.ConfigResponse
import ai.coreline.heybot.model.DashboardStatusResponse
import ai.coreline.heybot.model.ReplyRequest
import ai.coreline.heybot.model.ReplyType

/**
 * Local-only maintenance server. Main creates it only after a separate admin
 * secret has passed validation; every route except the minimal health probe is
 * authenticated here rather than at individual call sites.
 */
class IrisServer(
    private val dbObserver: DBObserver,
    private val observerHelper: ObserverHelper,
    private val notificationReferer: String,
    private val wsBroadcastFlow: MutableSharedFlow<String>,
    private val security: IrisHttpSecuritySettings,
    private val selfTestRunner: SelfTestRunner? = null
) {
    private val sharedFlow = wsBroadcastFlow.asSharedFlow()

    fun startServer() {
        val authenticator = security.authenticator()
        embeddedServer(
            factory = Netty,
            host = security.host,
            port = Configurable.botSocketPort
        ) {
            install(WebSockets) {
                contentConverter = KotlinxWebsocketSerializationConverter(Json)
            }

            install(ContentNegotiation) {
                json()
            }

            install(StatusPages) {
                exception<Throwable> { call, _ ->
                    call.respond(
                        HttpStatusCode.InternalServerError,
                        CommonErrorResponse(message = "INTERNAL_ERROR")
                    )
                }
            }

            routing {
                get("/health") {
                    call.respond(mapOf("ok" to true))
                }

                get("/self-test") {
                    val runner = selfTestRunner
                    if (runner == null) {
                        call.respond(
                            HttpStatusCode.ServiceUnavailable,
                            CommonErrorResponse(message = "SELF_TEST_UNAVAILABLE")
                        )
                        return@get
                    }
                    val mode = SelfTestMode.parse(call.request.queryParameters["mode"])
                    if (mode == null) {
                        call.respond(
                            HttpStatusCode.BadRequest,
                            CommonErrorResponse(message = "SELF_TEST_MODE_INVALID")
                        )
                        return@get
                    }
                    call.respond(runner.run(mode))
                }

                // The anonymous allowlist ends at /health. This interceptor also
                // protects new HTTP and WebSocket routes added in the future.
                intercept(ApplicationCallPipeline.Plugins) {
                    if (
                        call.request.path() != "/health" &&
                        !authenticator.isAuthorized(call.request.headers[HttpHeaders.Authorization])
                    ) {
                        call.respond(
                            HttpStatusCode.Unauthorized,
                            CommonErrorResponse(message = "UNAUTHORIZED")
                        )
                        finish()
                    }
                }

                route("/dashboard") {
                    get {
                        val html = PageRenderer.renderDashboard()
                        call.respondText(html, ContentType.Text.Html)
                    }

                    get("status") {
                        call.respond(
                            DashboardStatusResponse(
                                isObserving = dbObserver.isPollingThreadAlive,
                                statusMessage = if (dbObserver.isPollingThreadAlive) {
                                    "Observing database"
                                } else {
                                    "Not observing database"
                                },
                                lastLogs = observerHelper.lastChatLogs
                            )
                        )
                    }
                }

                route("/config") {
                    get {
                        call.respond(
                            ConfigResponse(
                                bot_name = Configurable.botName,
                                bot_http_port = Configurable.botSocketPort,
                                web_server_endpoint = Configurable.webServerEndpoint,
                                db_polling_rate = Configurable.dbPollingRate,
                                message_send_rate = Configurable.messageSendRate,
                                bot_id = Configurable.botId,
                            )
                        )
                    }

                    post("{name}") {
                        val name = call.parameters["name"]
                        val req = call.receive<ConfigRequest>()

                        when (name) {
                            // A mutable exfiltration URL does not belong in the
                            // production management surface.
                            "endpoint" -> {
                                call.respond(
                                    HttpStatusCode.Forbidden,
                                    CommonErrorResponse(message = "ENDPOINT_CONFIG_DISABLED")
                                )
                                return@post
                            }

                            "botname" -> {
                                val value = req.botname
                                if (value.isNullOrBlank()) {
                                    call.respond(
                                        HttpStatusCode.BadRequest,
                                        CommonErrorResponse(message = "INVALID_CONFIG_REQUEST")
                                    )
                                    return@post
                                }
                                Configurable.botName = value
                            }

                            "dbrate" -> {
                                val value = req.rate
                                if (value == null || value < 1) {
                                    call.respond(
                                        HttpStatusCode.BadRequest,
                                        CommonErrorResponse(message = "INVALID_CONFIG_REQUEST")
                                    )
                                    return@post
                                }
                                Configurable.dbPollingRate = value
                            }

                            "sendrate" -> {
                                val value = req.rate
                                if (value == null || value < 1) {
                                    call.respond(
                                        HttpStatusCode.BadRequest,
                                        CommonErrorResponse(message = "INVALID_CONFIG_REQUEST")
                                    )
                                    return@post
                                }
                                Configurable.messageSendRate = value
                            }

                            "botport" -> {
                                val value = req.port
                                if (value == null || value !in 1..65535) {
                                    call.respond(
                                        HttpStatusCode.BadRequest,
                                        CommonErrorResponse(message = "INVALID_CONFIG_REQUEST")
                                    )
                                    return@post
                                }
                                Configurable.botSocketPort = value
                            }

                            else -> {
                                call.respond(
                                    HttpStatusCode.NotFound,
                                    CommonErrorResponse(message = "NOT_FOUND")
                                )
                                return@post
                            }
                        }

                        call.respond(ApiResponse(success = true, message = "success"))
                    }
                }

                post("/reply") {
                    val replyRequest = call.receive<ReplyRequest>()
                    val roomId = replyRequest.room.toLong()
                    val threadId = replyRequest.threadId?.toLong()

                    when (replyRequest.type) {
                        ReplyType.TEXT -> Replier.sendMessage(
                            notificationReferer,
                            roomId,
                            replyRequest.data.jsonPrimitive.content,
                            threadId
                        )

                        ReplyType.IMAGE -> Replier.sendPhoto(
                            roomId, replyRequest.data.jsonPrimitive.content
                        )

                        ReplyType.IMAGE_MULTIPLE -> Replier.sendMultiplePhotos(
                            roomId,
                            replyRequest.data.jsonArray.map { it.jsonPrimitive.content }
                        )

                        ReplyType.VIDEO -> Replier.sendVideo(
                            roomId, replyRequest.data.jsonPrimitive.content
                        )
                    }

                    call.respond(ApiResponse(success = true, message = "success"))
                }

                webSocket("/ws") {
                    sharedFlow.collect { msg ->
                        send(msg)
                    }
                }
            }
        }.start(wait = true)
    }
}
