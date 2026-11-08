package com.series76.assistant.data.api.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

// What one assistant turn did internally: TraceOut in backend/app/schemas/conversation.py, its
// records in backend/app/schemas/trace.py. Only for the Debug screen (developer role); never
// shown in the chat.

@Serializable
data class TraceOut(
    @SerialName("message_id") val messageId: String,
    @SerialName("session_id") val sessionId: String,
    @SerialName("tool_calls") val toolCalls: List<ToolCallRecord>,
    val usage: List<UsageRecord>,
    val payload: JsonObject,
)

@Serializable
data class ToolCallRecord(
    val seq: Int,
    @SerialName("tool_name") val toolName: String,
    val args: JsonObject,
    /** The tool's typed result as JSON; null when the tool failed. */
    val result: JsonObject? = null,
    @SerialName("latency_ms") val latencyMs: Int,
    val error: String? = null,
)

@Serializable
data class UsageRecord(
    val model: String,
    @SerialName("input_tokens") val inputTokens: Int? = null,
    @SerialName("output_tokens") val outputTokens: Int? = null,
    /** A Decimal, sent as a string; kept as written (no rounding). Null: not reported. */
    @SerialName("cost_usd") val costUsd: JsonPrimitive? = null,
)
