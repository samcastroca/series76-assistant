package com.series76.assistant.data.api.model

import com.series76.assistant.data.api.InstantSerializer
import java.time.Instant
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

// Request and response bodies of the REST API. Defaults mirror the Pydantic models.

@Serializable
data class ConversationRequest(
    @SerialName("session_id") val sessionId: String? = null,
    val query: String,
)

@Serializable
data class ConversationResponse(
    @SerialName("session_id") val sessionId: String,
    @SerialName("message_id") val messageId: String,
    val answer: String,
    @SerialName("response_type") val responseType: ResponseType,
    val products: List<Product> = emptyList(),
    @SerialName("highlight_fields") val highlightFields: List<HighlightField> = emptyList(),
    val clarification: Clarification? = null,
    val suggestions: List<String> = emptyList(),
    val warnings: List<TurnWarning> = emptyList(),
)

@Serializable
data class Clarification(
    val question: String,
    val options: List<String>,
)

@Serializable
data class SessionSummary(
    val id: String,
    val title: String,
    @SerialName("created_at") @Serializable(with = InstantSerializer::class) val createdAt: Instant,
    @SerialName("updated_at") @Serializable(with = InstantSerializer::class) val updatedAt: Instant,
    @SerialName("message_count") val messageCount: Int,
)

@Serializable
data class MessageOut(
    val id: String,
    val role: Role,
    val content: String,
    val status: MessageStatus,
    @SerialName("created_at") @Serializable(with = InstantSerializer::class) val createdAt: Instant,
    @SerialName("response_type") val responseType: ResponseType? = null,
    val products: List<Product> = emptyList(),
    @SerialName("highlight_fields") val highlightFields: List<HighlightField> = emptyList(),
    val clarification: Clarification? = null,
    val suggestions: List<String> = emptyList(),
    val warnings: List<TurnWarning> = emptyList(),
)

/** The single error shape: `{"error": {"code", "message"}}`. */
@Serializable
data class ApiErrorBody(val error: ApiErrorDetail)

@Serializable
data class ApiErrorDetail(val code: String, val message: String)
