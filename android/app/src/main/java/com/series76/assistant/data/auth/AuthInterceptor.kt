package com.series76.assistant.data.auth

import okhttp3.Interceptor
import okhttp3.Response

/**
 * Sends `Authorization: Bearer <token>` on every request but the login, and reports any 401 (with
 * the token the request carried, or null) so the session ends and the app returns to Login. The
 * login's own 401 (`invalid_credentials`) is left to the login form.
 */
class AuthInterceptor(
    private val token: () -> String?,
    private val onUnauthorized: (tokenUsed: String?) -> Unit,
) : Interceptor {

    override fun intercept(chain: Interceptor.Chain): Response {
        val request = chain.request()
        if (request.url.encodedPath.endsWith(LOGIN_PATH)) return chain.proceed(request)
        val current = token()
        val response = chain.proceed(
            if (current == null) request else request.newBuilder().header("Authorization", "Bearer $current").build(),
        )
        if (response.code == 401) onUnauthorized(current)
        return response
    }

    private companion object {
        const val LOGIN_PATH = "/api/auth/login"
    }
}
