package com.series76.assistant.data.auth

import com.series76.assistant.data.ApiCall
import com.series76.assistant.data.ApiResult
import com.series76.assistant.data.api.Series76Api
import com.series76.assistant.data.api.model.LoginRequest
import com.series76.assistant.data.api.model.LoginResponse
import com.series76.assistant.data.api.model.UserOut

/** The two auth endpoints, with failures already classified. The seam the tests fake. */
interface AuthApi {
    suspend fun login(email: String, password: String): ApiResult<LoginResponse>

    /** Validates the current token (sent by [AuthInterceptor]) and returns its user. */
    suspend fun me(): ApiResult<UserOut>
}

class NetworkAuthApi(private val api: Series76Api, private val call: ApiCall) : AuthApi {
    override suspend fun login(email: String, password: String) =
        call { api.login(LoginRequest(email.trim(), password)) }

    override suspend fun me() = call { api.me() }
}
