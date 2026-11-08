package com.series76.assistant.ui.sessions

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.series76.assistant.data.ApiResult
import com.series76.assistant.data.AppError
import com.series76.assistant.data.ConversationRepository
import com.series76.assistant.data.api.model.SessionSummary
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

sealed interface SessionsUiState {
    data object Loading : SessionsUiState

    data class Error(val error: AppError) : SessionsUiState

    data class Content(
        val sessions: List<SessionSummary>,
        val isRefreshing: Boolean = false,
        /** Swiped and waiting for confirmation in a dialog. */
        val pendingDelete: SessionSummary? = null,
        /** A refresh or delete that failed while the list stayed visible (shown as a snackbar). */
        val transientError: TransientError? = null,
    ) : SessionsUiState
}

data class TransientError(val error: AppError, val action: FailedAction)

enum class FailedAction { REFRESH, DELETE }

class SessionsViewModel(private val repository: ConversationRepository) : ViewModel() {
    private val _state = MutableStateFlow<SessionsUiState>(SessionsUiState.Loading)
    val state: StateFlow<SessionsUiState> = _state.asStateFlow()

    private var lastFailedDelete: SessionSummary? = null

    /** Deleted this screen's lifetime: a refresh that started before a delete must not revive it. */
    private val deletedIds = mutableSetOf<String>()

    /** Called each time the screen is shown (first open and back from a chat). */
    fun onShown() {
        if (_state.value is SessionsUiState.Content) refresh() else load()
    }

    /** Full-screen load, used first and by the error state's "Retry". */
    fun load() {
        _state.value = SessionsUiState.Loading
        viewModelScope.launch {
            _state.value = when (val result = repository.sessions()) {
                is ApiResult.Ok -> SessionsUiState.Content(result.value)
                is ApiResult.Err -> SessionsUiState.Error(result.error)
            }
        }
    }

    /** Pull to refresh: the list stays visible; a failure becomes a snackbar. */
    fun refresh() {
        val current = _state.value as? SessionsUiState.Content ?: return load()
        if (current.isRefreshing) return
        _state.value = current.copy(isRefreshing = true, transientError = null)
        viewModelScope.launch {
            val result = repository.sessions()
            updateContent {
                when (result) {
                    is ApiResult.Ok -> it.copy(
                        sessions = result.value.filterNot { s -> s.id in deletedIds },
                        isRefreshing = false,
                    )
                    is ApiResult.Err -> it.copy(
                        isRefreshing = false,
                        transientError = TransientError(result.error, FailedAction.REFRESH),
                    )
                }
            }
        }
    }

    fun requestDelete(session: SessionSummary) = updateContent { it.copy(pendingDelete = session) }

    fun cancelDelete() = updateContent { it.copy(pendingDelete = null) }

    fun confirmDelete() {
        val session = (_state.value as? SessionsUiState.Content)?.pendingDelete ?: return
        updateContent { it.copy(pendingDelete = null, transientError = null) }
        delete(session)
    }

    /** The snackbar's "Retry": repeats whatever failed. */
    fun retryTransient() {
        val failed = (_state.value as? SessionsUiState.Content)?.transientError ?: return
        updateContent { it.copy(transientError = null) }
        when (failed.action) {
            FailedAction.REFRESH -> refresh()
            FailedAction.DELETE -> lastFailedDelete?.let(::delete)
        }
    }

    fun transientErrorShown() = updateContent { it.copy(transientError = null) }

    private fun delete(session: SessionSummary) {
        viewModelScope.launch {
            val result = repository.deleteSession(session.id)
            val error = (result as? ApiResult.Err)?.error
            // 404: someone else deleted it already, which is the outcome the user asked for.
            val gone = error == null || error == AppError.Client(404, "session_not_found")
            updateContent {
                if (gone) {
                    deletedIds += session.id
                    it.copy(sessions = it.sessions.filterNot { s -> s.id == session.id })
                } else {
                    lastFailedDelete = session
                    it.copy(transientError = TransientError(error!!, FailedAction.DELETE))
                }
            }
        }
    }

    private fun updateContent(transform: (SessionsUiState.Content) -> SessionsUiState.Content) {
        _state.update { if (it is SessionsUiState.Content) transform(it) else it }
    }
}
