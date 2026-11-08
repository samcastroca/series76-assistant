package com.series76.assistant.data

import com.series76.assistant.data.api.Series76Api
import com.series76.assistant.data.api.model.ConversationRequest
import com.series76.assistant.data.api.model.ConversationResponse
import com.series76.assistant.data.api.model.MessageOut
import com.series76.assistant.data.api.model.SessionSummary
import retrofit2.HttpException

interface ConversationRepository {
    suspend fun send(sessionId: String?, query: String): ApiResult<ConversationResponse>

    suspend fun sessions(): ApiResult<List<SessionSummary>>

    suspend fun messages(sessionId: String): ApiResult<List<MessageOut>>

    suspend fun deleteSession(sessionId: String): ApiResult<Unit>
}

class NetworkConversationRepository(
    private val api: Series76Api,
    private val call: ApiCall,
) : ConversationRepository {

    override suspend fun send(sessionId: String?, query: String) =
        call { api.postConversation(ConversationRequest(sessionId, query)) }

    override suspend fun sessions() = call { api.getSessions() }

    override suspend fun messages(sessionId: String) = call { api.getMessages(sessionId) }

    override suspend fun deleteSession(sessionId: String) = call {
        val response = api.deleteSession(sessionId)
        if (!response.isSuccessful) throw HttpException(response)
    }
}
