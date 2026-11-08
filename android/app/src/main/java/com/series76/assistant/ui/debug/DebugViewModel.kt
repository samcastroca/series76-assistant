package com.series76.assistant.ui.debug

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.series76.assistant.data.ApiResult
import com.series76.assistant.data.AppError
import com.series76.assistant.data.ConversationRepository
import com.series76.assistant.data.DebugRepository
import com.series76.assistant.data.api.model.Role
import com.series76.assistant.data.api.model.TraceOut
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/** One assistant answer of the conversation and what it did internally. */
data class DebugEntry(val messageId: String, val answer: String, val trace: TraceOut)

sealed interface DebugUiState {
    data object Loading : DebugUiState

    /** 403 `forbidden`: the user is not a developer. Final: nothing is retried. */
    data object Forbidden : DebugUiState

    data class Error(val error: AppError) : DebugUiState

    /** [focusIndex]: the entry opened from a message's bug icon, if any. */
    data class Content(val entries: List<DebugEntry>, val focusIndex: Int? = null) : DebugUiState
}

/**
 * The traces of every assistant answer in a conversation, in order. The answers come from the
 * stored history; each trace from `GET /api/messages/{id}/trace`.
 */
class DebugViewModel(
    private val sessionId: String,
    private val focusMessageId: String?,
    private val conversations: ConversationRepository,
    private val debug: DebugRepository,
) : ViewModel() {
    private val _state = MutableStateFlow<DebugUiState>(DebugUiState.Loading)
    val state: StateFlow<DebugUiState> = _state.asStateFlow()

    init {
        load()
    }

    /** Only after a retryable failure; a 403 is final. */
    fun retry() {
        if (_state.value is DebugUiState.Error) load()
    }

    private fun load() {
        _state.value = DebugUiState.Loading
        viewModelScope.launch { _state.value = fetch() }
    }

    private suspend fun fetch(): DebugUiState {
        val answers = when (val result = conversations.messages(sessionId)) {
            is ApiResult.Ok -> result.value.filter { it.role == Role.ASSISTANT }
            is ApiResult.Err -> return failure(result.error)
        }
        val entries = answers.map { message ->
            when (val result = debug.trace(message.id)) {
                is ApiResult.Ok -> DebugEntry(message.id, message.content, result.value)
                is ApiResult.Err -> return failure(result.error) // stop: no more requests
            }
        }
        val focus = entries.indexOfFirst { it.messageId == focusMessageId }.takeIf { it >= 0 }
        return DebugUiState.Content(entries, focus)
    }

    private fun failure(error: AppError): DebugUiState =
        if (error is AppError.Client && error.httpStatus == 403) DebugUiState.Forbidden else DebugUiState.Error(error)
}
