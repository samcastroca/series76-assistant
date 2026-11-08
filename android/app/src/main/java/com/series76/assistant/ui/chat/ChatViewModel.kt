package com.series76.assistant.ui.chat

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.series76.assistant.data.ApiResult
import com.series76.assistant.data.AppError
import com.series76.assistant.data.ConversationRepository
import java.util.UUID
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

sealed interface ChatUiState {
    /** Loading the history of an existing conversation. */
    data object Loading : ChatUiState

    /** The history could not be loaded; the screen offers "Retry". */
    data class Error(val error: AppError) : ChatUiState

    data class Content(
        val items: List<ChatItem> = emptyList(),
        val input: String = "",
        /** A turn is in flight: the "typing" bubble is shown and sending is disabled. */
        val isSending: Boolean = false,
        /** The last send failed because the device is offline (banner). */
        val offline: Boolean = false,
    ) : ChatUiState {
        val canSend: Boolean get() = !isSending && input.isNotBlank()
    }
}

/**
 * One conversation. [sessionId] is null for a new one; it is set from the first response, so a
 * retry or a follow-up goes to the same session.
 */
class ChatViewModel(
    sessionId: String?,
    private val repository: ConversationRepository,
    private val newId: () -> String = { UUID.randomUUID().toString() },
) : ViewModel() {
    var sessionId: String? = sessionId
        private set

    private val _state = MutableStateFlow<ChatUiState>(
        if (sessionId == null) ChatUiState.Content() else ChatUiState.Loading,
    )
    val state: StateFlow<ChatUiState> = _state.asStateFlow()

    init {
        if (sessionId != null) loadHistory()
    }

    fun loadHistory() {
        val id = sessionId ?: return
        _state.value = ChatUiState.Loading
        viewModelScope.launch {
            _state.value = when (val result = repository.messages(id)) {
                is ApiResult.Ok -> ChatUiState.Content(items = result.value.map { it.toChatItem() })
                is ApiResult.Err -> ChatUiState.Error(result.error)
            }
        }
    }

    fun onInputChange(text: String) = updateContent { it.copy(input = text) }

    /** Sends the typed text. */
    fun send() {
        val content = _state.value as? ChatUiState.Content ?: return
        if (!content.canSend) return
        val text = content.input.trim()
        updateContent { it.copy(input = "") }
        submit(ChatItem.User(id = newId(), text = text))
    }

    /** A clarification option or a suggestion chip: sent as the next query. */
    fun sendChip(text: String) {
        val content = _state.value as? ChatUiState.Content ?: return
        if (content.isSending) return
        submit(ChatItem.User(id = newId(), text = text))
    }

    /**
     * "Retry" on a failed user bubble: the same query to the same session. After a timeout or a
     * dropped connection the backend may have finished and stored the turn anyway, so in an
     * existing session the stored history is checked first; resending would duplicate the turn
     * (and pay for the model twice). A new chat has no session id to check: it resends.
     */
    fun retry(itemId: String) {
        val content = _state.value as? ChatUiState.Content ?: return
        if (content.isSending) return
        val failed = content.items.lastOrNull() as? ChatItem.User ?: return
        if (failed.id != itemId || !failed.failed) return
        val pending = failed.copy(failed = false, error = null)
        val id = sessionId
        if (id == null || failed.error !in AMBIGUOUS) return submit(pending, replace = true)

        updateContent { it.copy(items = it.items.dropLast(1) + pending, isSending = true) }
        viewModelScope.launch {
            val history = (repository.messages(id) as? ApiResult.Ok)?.value?.map { it.toChatItem() }
            if (history != null && history.answered(failed.text)) {
                updateContent { it.copy(items = history, isSending = false, offline = false) }
            } else {
                send(pending)
            }
        }
    }

    private fun submit(message: ChatItem.User, replace: Boolean = false) {
        updateContent {
            val items = if (replace) it.items.dropLast(1) + message else it.items + message
            it.copy(items = items, isSending = true)
        }
        viewModelScope.launch { send(message) }
    }

    /** Sends [message], already shown with `isSending = true`, and applies the outcome. */
    private suspend fun send(message: ChatItem.User) {
        when (val result = repository.send(sessionId, message.text)) {
            is ApiResult.Ok -> {
                sessionId = result.value.sessionId
                updateContent {
                    it.copy(items = it.items + result.value.toChatItem(), isSending = false, offline = false)
                }
            }
            is ApiResult.Err -> updateContent {
                val failed = message.copy(failed = true, error = result.error)
                it.copy(
                    items = it.items.map { item -> if (item.id == message.id) failed else item },
                    isSending = false,
                    offline = result.error == AppError.Offline,
                )
            }
        }
    }

    private fun updateContent(transform: (ChatUiState.Content) -> ChatUiState.Content) {
        _state.update { if (it is ChatUiState.Content) transform(it) else it }
    }

    private companion object {
        /** Failures after which the backend may still have stored the turn. */
        val AMBIGUOUS = setOf(AppError.Timeout, AppError.Unreachable)
    }
}

/** The history ends with an answer to [query]: the turn was stored despite the failure. */
private fun List<ChatItem>.answered(query: String): Boolean {
    val answer = lastOrNull() as? ChatItem.Assistant ?: return false
    val question = getOrNull(size - 2) as? ChatItem.User ?: return false
    return answer.answer.isNotEmpty() && question.text == query && !question.failed
}
