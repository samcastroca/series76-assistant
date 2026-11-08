package com.series76.assistant.data.auth

import com.series76.assistant.data.ApiResult
import com.series76.assistant.data.AppError
import com.series76.assistant.data.api.model.UserOut
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference
import java.util.logging.Level
import java.util.logging.Logger
import kotlin.coroutines.cancellation.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/** Where the app stands with the backend. The UI root renders one screen per state. */
sealed interface AuthState {
    /** Reading the stored session or validating it with GET /api/auth/me. */
    data object Checking : AuthState

    /** No valid token: the Login screen. */
    data object SignedOut : AuthState

    data class SignedIn(val user: UserOut) : AuthState

    /** A stored token could not be validated for a reason other than 401 (offline, server down). */
    data class VerifyFailed(val error: AppError) : AuthState
}

/**
 * The app-wide session: one instance, created by [com.series76.assistant.AppContainer].
 *
 * - [start] restores a stored session and validates it with `/me` before entering; only a 401
 *   discards it, any other failure leaves [AuthState.VerifyFailed] with [retry].
 * - [onUnauthorized] is called by [AuthInterceptor] for any 401: the session ends and the UI
 *   falls back to Login. A late 401 for an older token is ignored.
 * - The password is never stored; the role comes from the server (`/me` refreshes it).
 *
 * Token and state change together and synchronously; validation and disk writes run in [scope]
 * (the app's), never in a screen's, so closing a screen cannot leave them half done. Disk writes
 * are serialized and never undo a newer session. A store failure is logged and the session
 * still works for this process.
 */
class SessionManager(
    private val api: AuthApi,
    private val store: SessionStore,
    private val scope: CoroutineScope,
) {
    private val _state = MutableStateFlow<AuthState>(AuthState.Checking)
    val state: StateFlow<AuthState> = _state.asStateFlow()

    private val current = AtomicReference<String?>(null)

    /** The token [AuthInterceptor] sends. Read from OkHttp threads. */
    val token: String? get() = current.get()

    private val started = AtomicBoolean(false)
    private val writes = Mutex()

    /** Restores and validates the stored session once per process; later calls do nothing. */
    fun start() {
        if (started.compareAndSet(false, true)) scope.launch { verifyStored() }
    }

    /** Validates again after [AuthState.VerifyFailed]; ignored in any other state (double taps). */
    fun retry() {
        val failed = _state.value as? AuthState.VerifyFailed ?: return
        if (_state.compareAndSet(failed, AuthState.Checking)) scope.launch { verifyStored() }
    }

    /** Signs in and keeps the session; the caller only waits for the network call. */
    suspend fun login(email: String, password: String): ApiResult<UserOut> =
        when (val result = api.login(email, password)) {
            is ApiResult.Ok -> {
                val session = result.value
                current.set(session.accessToken)
                _state.value = AuthState.SignedIn(session.user)
                persist(StoredSession(session.accessToken, session.user.email, session.user.role))
                ApiResult.Ok(session.user)
            }
            is ApiResult.Err -> result
        }

    fun logout() = end(current.get())

    /**
     * A request was answered 401. [tokenUsed] is the token it carried (null: it carried none). The
     * session ends only if that token is still the current one, or if a signed-in screen somehow
     * made a call without a token.
     */
    fun onUnauthorized(tokenUsed: String?) {
        if (tokenUsed == null) {
            if (_state.value is AuthState.SignedIn && current.get() == null) end(null)
            return
        }
        if (current.get() == tokenUsed) end(tokenUsed)
    }

    private fun end(token: String?) {
        if (!current.compareAndSet(token, null)) return // a newer session started meanwhile
        _state.value = AuthState.SignedOut
        scope.launch {
            writes.withLock {
                if (current.get() == null) safely("clear") { store.clear() } // unless signed in again
            }
        }
    }

    private fun persist(session: StoredSession) {
        scope.launch {
            writes.withLock {
                if (current.get() == session.token) safely("save") { store.save(session) } // still current
            }
        }
    }

    private suspend fun verifyStored() {
        _state.value = AuthState.Checking
        val stored = safely("read") { store.read() }
        if (stored == null) {
            _state.value = AuthState.SignedOut
            return
        }
        current.set(stored.token)
        when (val result = api.me()) {
            is ApiResult.Ok -> {
                _state.value = AuthState.SignedIn(result.value)
                persist(stored.copy(email = result.value.email, role = result.value.role))
            }
            is ApiResult.Err -> if (result.error.isUnauthorized) {
                end(stored.token)
                _state.value = AuthState.SignedOut // also when the interceptor already ended it
            } else {
                _state.value = AuthState.VerifyFailed(result.error)
            }
        }
    }

    private suspend fun <T> safely(action: String, block: suspend () -> T): T? =
        try {
            block()
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) { // IOException, GeneralSecurityException (Keystore), ...
            log.log(Level.WARNING, "session store $action failed; continuing without it", e)
            null
        }

    private companion object {
        val log: Logger = Logger.getLogger(SessionManager::class.java.name)
    }
}

private val AppError.isUnauthorized: Boolean get() = this is AppError.Client && httpStatus == 401
