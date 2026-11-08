package com.series76.assistant.data

import com.series76.assistant.data.api.Series76Api
import com.series76.assistant.data.api.model.TraceOut

/** The trace of one assistant message (developer role; 403 `forbidden` otherwise). */
interface DebugRepository {
    suspend fun trace(messageId: String): ApiResult<TraceOut>
}

class NetworkDebugRepository(private val api: Series76Api, private val call: ApiCall) : DebugRepository {
    override suspend fun trace(messageId: String) = call { api.getTrace(messageId) }
}
