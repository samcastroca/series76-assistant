package com.series76.assistant.data

import com.series76.assistant.data.api.Series76Api
import com.series76.assistant.data.api.model.ApiErrorBody
import com.series76.assistant.data.api.model.ConversationRequest
import com.series76.assistant.data.api.model.ConversationResponse
import com.series76.assistant.data.api.model.MessageOut
import com.series76.assistant.data.api.model.SessionSummary
import java.io.IOException
import java.io.InterruptedIOException
import kotlin.coroutines.cancellation.CancellationException
import kotlinx.serialization.SerializationException
import kotlinx.serialization.json.Json
import okhttp3.ResponseBody
import retrofit2.HttpException

interface ConversationRepository {
    suspend fun send(sessionId: String?, query: String): ApiResult<ConversationResponse>

    suspend fun sessions(): ApiResult<List<SessionSummary>>

    suspend fun messages(sessionId: String): ApiResult<List<MessageOut>>

    suspend fun deleteSession(sessionId: String): ApiResult<Unit>
}

class NetworkConversationRepository(
    private val api: Series76Api,
    private val json: Json,
    private val connectivity: ConnectivityChecker,
) : ConversationRepository {

    override suspend fun send(sessionId: String?, query: String) =
        call { api.postConversation(ConversationRequest(sessionId, query)) }

    override suspend fun sessions() = call { api.getSessions() }

    override suspend fun messages(sessionId: String) = call { api.getMessages(sessionId) }

    override suspend fun deleteSession(sessionId: String) = call {
        val response = api.deleteSession(sessionId)
        if (!response.isSuccessful) throw HttpException(response)
    }

    private suspend fun <T> call(block: suspend () -> T): ApiResult<T> =
        try {
            ApiResult.Ok(block())
        } catch (e: CancellationException) {
            throw e
        } catch (e: HttpException) {
            ApiResult.Err(httpError(e.code(), e.response()?.errorBody()))
        } catch (e: SerializationException) {
            ApiResult.Err(AppError.Unexpected)
        } catch (e: IOException) {
            ApiResult.Err(ioError(e))
        }

    private fun ioError(e: IOException): AppError = when {
        !connectivity.isOnline() -> AppError.Offline
        e is InterruptedIOException -> AppError.Timeout // SocketTimeoutException and call timeouts
        else -> AppError.Unreachable
    }

    private fun httpError(status: Int, body: ResponseBody?): AppError {
        val code = try {
            body?.use { readCode(it.string()) }
        } catch (e: IOException) {
            null // the status alone still classifies the failure
        }
        return when {
            status == 503 && code == "budget_exceeded" -> AppError.BudgetExceeded
            status == 503 && code == "llm_unavailable" -> AppError.AssistantUnavailable
            status >= 500 -> AppError.Server(status)
            else -> AppError.Client(status, code)
        }
    }

    private fun readCode(text: String): String? =
        try {
            json.decodeFromString<ApiErrorBody>(text).error.code
        } catch (e: IllegalArgumentException) { // includes SerializationException
            null // not the API's error shape (for example a proxy page)
        }
}
