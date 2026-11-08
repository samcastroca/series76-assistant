package com.series76.assistant.data.auth

import com.series76.assistant.data.ApiCall
import com.series76.assistant.data.NetworkConversationRepository
import com.series76.assistant.data.api.ApiJson
import com.series76.assistant.data.api.Series76Api
import com.series76.assistant.testutil.Fixtures
import com.series76.assistant.testutil.InMemorySessionStore
import com.series76.assistant.withSession
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import retrofit2.Retrofit
import retrofit2.converter.kotlinx.serialization.asConverterFactory

/**
 * The real chain the app builds (OkHttp + [withSession] + Retrofit + [SessionManager]) against an
 * HTTP server: the login's token goes on the next call, and a 401 there ends the session.
 */
class SessionWiringTest {
    private val server = MockWebServer()

    @Before
    fun start() = server.start()

    @After
    fun stop() = server.close()

    private fun respond(status: Int, fixture: String) =
        server.enqueue(MockResponse.Builder().code(status).body(Fixtures.text(fixture)).build())

    @Test
    fun `a 401 on any call ends the session and clears the store`() = runTest {
        lateinit var sessions: SessionManager
        val http = OkHttpClient.Builder().withSession { sessions }.build()
        val api = Retrofit.Builder()
            .baseUrl(server.url("/"))
            .client(http)
            .addConverterFactory(ApiJson.asConverterFactory("application/json".toMediaType()))
            .build()
            .create(Series76Api::class.java)
        val call = ApiCall(ApiJson) { true }
        val store = InMemorySessionStore()
        sessions = SessionManager(NetworkAuthApi(api, call), store, this)
        val conversations = NetworkConversationRepository(api, call)

        respond(200, "auth_login")
        sessions.login("engineer@example.com", "pw")
        advanceUntilIdle()
        assertTrue(sessions.state.value is AuthState.SignedIn)
        server.takeRequest() // the login

        respond(401, "error_not_authenticated")
        conversations.sessions()
        val sent = server.takeRequest()
        assertEquals("Bearer ${Fixtures.login().accessToken}", sent.headers["Authorization"])

        advanceUntilIdle()
        assertEquals(AuthState.SignedOut, sessions.state.value)
        assertNull(sessions.token)
        assertNull(store.session)
    }
}
