package com.series76.assistant.testutil

import com.series76.assistant.data.ApiResult
import com.series76.assistant.data.AppError
import com.series76.assistant.data.api.model.LoginResponse
import com.series76.assistant.data.api.model.UserOut
import com.series76.assistant.data.auth.AuthApi
import com.series76.assistant.data.auth.SessionStore
import com.series76.assistant.data.auth.StoredSession
import kotlinx.coroutines.CompletableDeferred

/**
 * Auth endpoints answered from the generated fixtures (`auth_login`, `auth_me`). Each call takes
 * the next queued result, or the fixture when the queue is empty; calls are recorded.
 */
class FakeAuthApi : AuthApi {
    val loginResults = ArrayDeque<ApiResult<LoginResponse>>()
    val meResults = ArrayDeque<ApiResult<UserOut>>()
    val logins = mutableListOf<Pair<String, String>>()
    var meCalls = 0
        private set

    /** When set, `login()` / `me()` wait for it: lets a test act while the call is in flight. */
    var loginGate: CompletableDeferred<Unit>? = null
    var meGate: CompletableDeferred<Unit>? = null

    override suspend fun login(email: String, password: String): ApiResult<LoginResponse> {
        logins += email to password
        loginGate?.await()
        return loginResults.removeFirstOrNull() ?: ApiResult.Ok(Fixtures.login())
    }

    override suspend fun me(): ApiResult<UserOut> {
        meCalls++
        meGate?.await()
        return meResults.removeFirstOrNull() ?: ApiResult.Ok(Fixtures.me())
    }

    companion object {
        fun unauthorized(code: String) = ApiResult.Err(AppError.Client(401, code))

        fun loginWith(token: String) = ApiResult.Ok(Fixtures.login().copy(accessToken = token))
    }
}

/** The encrypted store's contract, in memory. */
class InMemorySessionStore(var session: StoredSession? = null) : SessionStore {
    override suspend fun read() = session

    override suspend fun save(session: StoredSession) {
        this.session = session
    }

    override suspend fun clear() {
        session = null
    }
}

/** A store whose disk always fails (Keystore or I/O problems). */
class FailingSessionStore : SessionStore {
    override suspend fun read(): StoredSession? = throw java.io.IOException("disk")

    override suspend fun save(session: StoredSession) = throw java.security.GeneralSecurityException("keystore")

    override suspend fun clear() = throw java.io.IOException("disk")
}
