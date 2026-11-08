package com.series76.assistant.data.api

import com.series76.assistant.data.api.model.ConversationRequest
import com.series76.assistant.data.api.model.ConversationResponse
import com.series76.assistant.data.api.model.MessageOut
import com.series76.assistant.data.api.model.SessionSummary
import retrofit2.Response
import retrofit2.http.Body
import retrofit2.http.DELETE
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.Path

interface Series76Api {
    @POST("api/conversation")
    suspend fun postConversation(@Body body: ConversationRequest): ConversationResponse

    @GET("api/sessions")
    suspend fun getSessions(): List<SessionSummary>

    @GET("api/sessions/{id}/messages")
    suspend fun getMessages(@Path("id") sessionId: String): List<MessageOut>

    @DELETE("api/sessions/{id}")
    suspend fun deleteSession(@Path("id") sessionId: String): Response<Unit>
}
