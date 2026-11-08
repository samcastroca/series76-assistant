package com.series76.assistant.data

import com.series76.assistant.data.api.model.ApiErrorBody
import java.io.IOException
import java.io.InterruptedIOException
import kotlin.coroutines.cancellation.CancellationException
import kotlinx.serialization.SerializationException
import kotlinx.serialization.json.Json
import okhttp3.ResponseBody
import retrofit2.HttpException

/**
 * Runs one API call and classifies its failure as an [AppError]. Shared by every repository so
 * a status or error `code` means the same thing on every screen.
 */
class ApiCall(private val json: Json, private val connectivity: ConnectivityChecker) {

    suspend operator fun <T> invoke(block: suspend () -> T): ApiResult<T> =
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
