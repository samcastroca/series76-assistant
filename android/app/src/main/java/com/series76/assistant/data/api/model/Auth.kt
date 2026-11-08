package com.series76.assistant.data.api.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

// Login and current-user bodies (backend/app/schemas/auth.py).

@Serializable
data class LoginRequest(val email: String, val password: String)

@Serializable
data class LoginResponse(
    @SerialName("access_token") val accessToken: String,
    @SerialName("token_type") val tokenType: String,
    val user: UserOut,
)

@Serializable
enum class UserRole {
    @SerialName("engineer") ENGINEER,
    @SerialName("developer") DEVELOPER,
}

@Serializable
data class UserOut(val email: String, val role: UserRole) {
    /** Only developers may open the Debug screen (the trace endpoint answers 403 otherwise). */
    val isDeveloper: Boolean get() = role == UserRole.DEVELOPER
}
