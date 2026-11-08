package com.series76.assistant.testutil

import com.series76.assistant.data.api.ApiJson
import com.series76.assistant.data.api.model.ConversationResponse
import com.series76.assistant.data.api.model.LoginResponse
import com.series76.assistant.data.api.model.MessageOut
import com.series76.assistant.data.api.model.SessionSummary
import com.series76.assistant.data.api.model.TraceOut
import com.series76.assistant.data.api.model.UserOut
import kotlinx.serialization.json.Json

/**
 * The JSON fixtures in src/test/resources, generated from the real API by
 * `make android-fixtures` (never hand-edited).
 */
object Fixtures {
    /** Fails on any key the Kotlin models do not declare: the contract tests use it. */
    val strictJson = Json { ignoreUnknownKeys = false }

    fun text(name: String): String {
        val stream = checkNotNull(javaClass.classLoader?.getResourceAsStream("$name.json")) {
            "missing fixture $name.json (run `make android-fixtures`)"
        }
        return stream.bufferedReader().use { it.readText() }
    }

    inline fun <reified T> load(name: String, json: Json = ApiJson): T = json.decodeFromString(text(name))

    fun conversation(responseType: String): ConversationResponse = load("conversation_$responseType")

    fun sessions(): List<SessionSummary> = load("sessions")

    fun messages(): List<MessageOut> = load("messages")

    fun login(): LoginResponse = load("auth_login")

    fun me(): UserOut = load("auth_me")

    fun trace(): TraceOut = load("trace")
}
