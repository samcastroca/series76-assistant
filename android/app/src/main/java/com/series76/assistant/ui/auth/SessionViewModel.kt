package com.series76.assistant.ui.auth

import androidx.lifecycle.ViewModel
import com.series76.assistant.data.auth.AuthState
import com.series76.assistant.data.auth.SessionManager
import kotlinx.coroutines.flow.StateFlow

/**
 * The UI root's view of the session: which screen to show, plus Retry (after a failed
 * validation) and sign-out. The work runs in the session's own scope, so it finishes even if
 * this screen goes away; starting is idempotent, so a configuration change does not validate the
 * token again.
 */
class SessionViewModel(private val sessions: SessionManager) : ViewModel() {
    val state: StateFlow<AuthState> = sessions.state

    init {
        sessions.start()
    }

    fun retry() = sessions.retry()

    fun logout() = sessions.logout()
}
