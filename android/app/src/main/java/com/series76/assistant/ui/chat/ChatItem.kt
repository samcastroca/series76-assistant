package com.series76.assistant.ui.chat

import com.series76.assistant.data.AppError
import com.series76.assistant.data.api.model.Clarification
import com.series76.assistant.data.api.model.ConversationResponse
import com.series76.assistant.data.api.model.MessageOut
import com.series76.assistant.data.api.model.MessageStatus
import com.series76.assistant.data.api.model.Product
import com.series76.assistant.data.api.model.ResponseType
import com.series76.assistant.data.api.model.Role
import com.series76.assistant.data.api.model.TurnWarning

/** One row of the chat, built from a live response or from the stored history alike. */
sealed interface ChatItem {
    val id: String

    data class User(
        override val id: String,
        val text: String,
        /** The send failed: the bubble offers "Retry". */
        val failed: Boolean = false,
        /** Why it failed, when known (a failure loaded from history has no detail). */
        val error: AppError? = null,
    ) : ChatItem

    data class Assistant(
        override val id: String,
        val answer: String,
        val responseType: ResponseType,
        val products: List<Product>,
        val clarification: Clarification?,
        val suggestions: List<String>,
        val warnings: List<TurnWarning>,
    ) : ChatItem
}

fun ConversationResponse.toChatItem() = ChatItem.Assistant(
    id = messageId,
    answer = answer,
    responseType = responseType,
    products = products,
    clarification = clarification,
    suggestions = suggestions,
    warnings = warnings,
)

/** Rebuilds the chat from `/api/sessions/{id}/messages`; cards come from each `products`. */
fun MessageOut.toChatItem(): ChatItem = when (role) {
    Role.USER -> ChatItem.User(id = id, text = content, failed = status == MessageStatus.ERROR)
    Role.ASSISTANT -> ChatItem.Assistant(
        id = id,
        answer = content,
        responseType = responseType ?: ResponseType.ERROR,
        products = products,
        clarification = clarification,
        suggestions = suggestions,
        warnings = warnings,
    )
}
