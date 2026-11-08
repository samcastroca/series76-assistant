package com.series76.assistant.data.auth

import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import okhttp3.OkHttpClient
import okhttp3.Request
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Before
import org.junit.Test

/** The Bearer header and the 401 report, against a real HTTP server. */
class AuthInterceptorTest {
    private val server = MockWebServer()
    private var token: String? = "t1"
    private val reported = mutableListOf<String?>()
    private val client = OkHttpClient.Builder()
        .addInterceptor(AuthInterceptor(token = { token }, onUnauthorized = { reported += it }))
        .build()

    @Before
    fun start() = server.start()

    @After
    fun stop() = server.close()

    private fun get(path: String, status: Int) {
        server.enqueue(MockResponse.Builder().code(status).body("{}").build())
        client.newCall(Request.Builder().url(server.url(path)).build()).execute().close()
    }

    @Test
    fun `sends the bearer token on API calls`() {
        get("/api/sessions", 200)
        assertEquals("Bearer t1", server.takeRequest().headers["Authorization"])
        assertEquals(emptyList<String?>(), reported)
    }

    @Test
    fun `never sends a token to the login endpoint`() {
        get("/api/auth/login", 200)
        assertNull(server.takeRequest().headers["Authorization"])
    }

    @Test
    fun `a 401 on an API call reports the token that was used`() {
        get("/api/sessions", 401)
        assertEquals(listOf("t1"), reported)
    }

    @Test
    fun `the login form handles the login endpoint's own 401`() {
        get("/api/auth/login", 401)
        assertEquals(emptyList<String?>(), reported)
    }

    @Test
    fun `without a token no header is sent and a 401 is still reported`() {
        token = null
        get("/api/sessions", 401)
        assertNull(server.takeRequest().headers["Authorization"])
        assertEquals(listOf<String?>(null), reported)
    }
}
