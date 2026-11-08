package com.series76.assistant.data

import com.series76.assistant.data.api.ApiJson
import com.series76.assistant.data.api.Series76Api
import com.series76.assistant.testutil.Fixtures
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import retrofit2.Retrofit
import retrofit2.converter.kotlinx.serialization.asConverterFactory

/** HTTP and I/O failures become the right [AppError]; bodies come from the generated fixtures. */
class NetworkConversationRepositoryTest {
    private val server = MockWebServer()
    private var online = true

    @Before
    fun start() = server.start()

    @After
    fun stop() = server.close()

    private fun repository(readTimeoutMs: Long = 2_000): NetworkConversationRepository {
        val http = OkHttpClient.Builder().readTimeout(readTimeoutMs, TimeUnit.MILLISECONDS).build()
        val api = Retrofit.Builder()
            .baseUrl(server.url("/"))
            .client(http)
            .addConverterFactory(ApiJson.asConverterFactory("application/json".toMediaType()))
            .build()
            .create(Series76Api::class.java)
        return NetworkConversationRepository(api, ApiJson) { online }
    }

    private fun enqueue(code: Int, fixture: String? = null, body: String? = null) {
        server.enqueue(
            MockResponse.Builder()
                .code(code)
                .addHeader("Content-Type", "application/json")
                .body(fixture?.let(Fixtures::text) ?: body ?: "")
                .build(),
        )
    }

    private fun <T> ApiResult<T>.error() = (this as ApiResult.Err).error

    @Test
    fun `a turn decodes and sends the session id and query`() = runTest {
        enqueue(200, "conversation_detail")
        val result = repository().send("abc", "datos")
        assertTrue(result is ApiResult.Ok)

        val request = server.takeRequest()
        assertEquals("POST", request.method)
        assertEquals("/api/conversation", request.url.encodedPath)
        val sent = Json.parseToJsonElement(request.body!!.utf8()).jsonObject
        assertEquals("abc", sent["session_id"]?.jsonPrimitive?.content)
        assertEquals("datos", sent["query"]?.jsonPrimitive?.content)
    }

    @Test
    fun `503 budget_exceeded is told apart from 503 llm_unavailable`() = runTest {
        enqueue(503, "error_budget_exceeded")
        enqueue(503, "error_llm_unavailable")
        val repo = repository()
        assertEquals(AppError.BudgetExceeded, repo.send(null, "q").error())
        assertEquals(AppError.AssistantUnavailable, repo.send(null, "q").error())
    }

    @Test
    fun `other 5xx are server errors, with or without the JSON body`() = runTest {
        enqueue(500, body = """{"error":{"code":"internal_error","message":"Something went wrong."}}""")
        enqueue(502, body = "<html>Bad Gateway</html>")
        val repo = repository()
        assertEquals(AppError.Server(500), repo.sessions().error())
        assertEquals(AppError.Server(502), repo.sessions().error())
    }

    @Test
    fun `4xx keeps the API error code and is not retryable`() = runTest {
        enqueue(404, "error_session_not_found")
        val error = repository().messages("missing").error()
        assertEquals(AppError.Client(404, "session_not_found"), error)
        assertTrue(!error.retryable)
    }

    @Test
    fun `a backend that does not answer in time is a timeout`() = runTest {
        server.enqueue(
            MockResponse.Builder().body(Fixtures.text("sessions")).headersDelay(1, TimeUnit.SECONDS).build(),
        )
        assertEquals(AppError.Timeout, repository(readTimeoutMs = 100).sessions().error())
    }

    @Test
    fun `a backend that is down is unreachable`() = runTest {
        val repo = repository()
        server.close() // nothing listens on the port any more
        assertEquals(AppError.Unreachable, repo.sessions().error())
    }

    @Test
    fun `an I-O failure without network is offline`() = runTest {
        val repo = repository()
        server.close()
        online = false
        assertEquals(AppError.Offline, repo.send(null, "q").error())
    }

    @Test
    fun `a body that breaks the contract is unexpected`() = runTest {
        enqueue(200, body = """[{"id": 1}]""")
        assertEquals(AppError.Unexpected, repository().sessions().error())
    }

    @Test
    fun `delete succeeds on 204 and maps a 404`() = runTest {
        server.enqueue(MockResponse.Builder().code(204).build())
        enqueue(404, "error_session_not_found")
        val repo = repository()
        assertTrue(repo.deleteSession("a") is ApiResult.Ok)
        assertEquals(AppError.Client(404, "session_not_found"), repo.deleteSession("a").error())
        assertEquals("DELETE", server.takeRequest().method)
    }
}
