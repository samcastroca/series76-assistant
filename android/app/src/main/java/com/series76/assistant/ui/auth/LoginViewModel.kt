package com.series76.assistant.ui.auth

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.series76.assistant.data.ApiResult
import com.series76.assistant.data.AppError
import com.series76.assistant.data.auth.SessionManager
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class LoginUiState(
    val email: String = "",
    val password: String = "",
    val passwordVisible: Boolean = false,
    val loading: Boolean = false,
    val error: AppError? = null,
) {
    /** The button is disabled while a login is in flight or a field is empty. */
    val canSubmit: Boolean get() = email.isNotBlank() && password.isNotEmpty() && !loading
}

/**
 * The login form. A successful login moves [SessionManager.state] to SignedIn and the UI root
 * leaves this screen, clearing this ViewModel ([BranchViewModelStores]); the password is never
 * stored.
 */
class LoginViewModel(private val sessions: SessionManager) : ViewModel() {
    private val _state = MutableStateFlow(LoginUiState())
    val state: StateFlow<LoginUiState> = _state.asStateFlow()

    fun onEmailChange(value: String) = _state.update { it.copy(email = value, error = null) }

    fun onPasswordChange(value: String) = _state.update { it.copy(password = value, error = null) }

    fun togglePasswordVisible() = _state.update { it.copy(passwordVisible = !it.passwordVisible) }

    fun submit() {
        val current = _state.value
        if (!current.canSubmit) return
        _state.update { it.copy(loading = true, error = null) }
        viewModelScope.launch {
            val result = sessions.login(current.email, current.password)
            _state.update {
                when (result) {
                    // Dropped from memory right away; the ViewModel itself goes when Login closes.
                    is ApiResult.Ok -> it.copy(loading = false, password = "", passwordVisible = false)
                    is ApiResult.Err -> it.copy(loading = false, error = result.error)
                }
            }
        }
    }
}
