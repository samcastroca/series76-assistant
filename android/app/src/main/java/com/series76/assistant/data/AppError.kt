package com.series76.assistant.data

/**
 * Why a request failed, in terms the UI can explain. Each case maps to a fixed user-facing
 * message (ui/common/ErrorText.kt); the backend's English `message` and any stack trace are never
 * shown.
 */
sealed interface AppError {
    /** The device has no network. */
    data object Offline : AppError

    /** The backend did not answer in time (down or overloaded). */
    data object Timeout : AppError

    /** The device is online but the backend refused or dropped the connection (down). */
    data object Unreachable : AppError

    /** 503 `budget_exceeded`: the daily LLM spend cap was reached. */
    data object BudgetExceeded : AppError

    /** 503 `llm_unavailable`: the language model provider failed. */
    data object AssistantUnavailable : AppError

    /** Any other 5xx (`internal_error`, or a proxy error without the JSON body). */
    data class Server(val httpStatus: Int) : AppError

    /** A 4xx with the API's error `code` (`session_not_found`, `query_too_long`, ...). */
    data class Client(val httpStatus: Int, val code: String?) : AppError

    /** A response the app could not read (contract mismatch) or another unexpected failure. */
    data object Unexpected : AppError

    /** Whether sending the same request again can succeed. A rejected request (4xx) cannot. */
    val retryable: Boolean get() = this !is Client
}

sealed interface ApiResult<out T> {
    data class Ok<T>(val value: T) : ApiResult<T>

    data class Err(val error: AppError) : ApiResult<Nothing>
}
