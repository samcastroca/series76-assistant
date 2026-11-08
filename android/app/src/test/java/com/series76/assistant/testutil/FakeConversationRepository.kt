package com.series76.assistant.testutil

import com.series76.assistant.data.ApiResult
import com.series76.assistant.data.AppError
import com.series76.assistant.data.ConversationRepository
import com.series76.assistant.data.api.model.ConversationResponse
import com.series76.assistant.data.api.model.MessageOut
import com.series76.assistant.data.api.model.SessionSummary
import kotlinx.coroutines.CompletableDeferred

/**
 * A repository that answers with the generated JSON fixtures. Each call takes the next queued
 * result for its endpoint, or the fixture default when the queue is empty; calls are recorded.
 */
class FakeConversationRepository : ConversationRepository {
    val sendResults = ArrayDeque<ApiResult<ConversationResponse>>()
    val sessionsResults = ArrayDeque<ApiResult<List<SessionSummary>>>()
    val messagesResults = ArrayDeque<ApiResult<List<MessageOut>>>()
    val deleteResults = ArrayDeque<ApiResult<Unit>>()

    val sent = mutableListOf<Pair<String?, String>>()
    val deleted = mutableListOf<String>()
    var sessionsCalls = 0
        private set

    /** When set, `sessions()` waits for it: lets a test finish another call first. */
    var sessionsGate: CompletableDeferred<Unit>? = null

    fun failNextSend(error: AppError) = sendResults.addLast(ApiResult.Err(error))

    override suspend fun send(sessionId: String?, query: String): ApiResult<ConversationResponse> {
        sent += sessionId to query
        return sendResults.removeFirstOrNull() ?: ApiResult.Ok(Fixtures.conversation("detail"))
    }

    override suspend fun sessions(): ApiResult<List<SessionSummary>> {
        sessionsCalls++
        sessionsGate?.await()
        return sessionsResults.removeFirstOrNull() ?: ApiResult.Ok(Fixtures.sessions())
    }

    override suspend fun messages(sessionId: String): ApiResult<List<MessageOut>> =
        messagesResults.removeFirstOrNull() ?: ApiResult.Ok(Fixtures.messages())

    override suspend fun deleteSession(sessionId: String): ApiResult<Unit> {
        deleted += sessionId
        return deleteResults.removeFirstOrNull() ?: ApiResult.Ok(Unit)
    }
}
