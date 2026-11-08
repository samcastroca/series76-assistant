package com.series76.assistant

import android.content.Context
import com.series76.assistant.data.AndroidConnectivityChecker
import com.series76.assistant.data.ApiCall
import com.series76.assistant.data.ConversationRepository
import com.series76.assistant.data.DebugRepository
import com.series76.assistant.data.NetworkConversationRepository
import com.series76.assistant.data.NetworkDebugRepository
import com.series76.assistant.data.api.ApiJson
import com.series76.assistant.data.api.Series76Api
import com.series76.assistant.data.auth.AuthInterceptor
import com.series76.assistant.data.auth.NetworkAuthApi
import com.series76.assistant.data.auth.SessionManager
import com.series76.assistant.data.auth.TinkSessionStore
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Retrofit
import retrofit2.converter.kotlinx.serialization.asConverterFactory

/** Manual dependency injection: the app-wide objects, created once in [Series76App]. */
class AppContainer(context: Context, baseUrl: String = BuildConfig.API_BASE_URL) {

    /** Outlives every screen: session clean-up after a 401 runs here. */
    private val appScope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    // The interceptor reads the session lazily: the manager needs the API, the API needs the client.
    private lateinit var sessions: SessionManager

    private val http = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        // A chat turn runs the agent (model calls, tools, one validator retry): allow for it.
        .readTimeout(90, TimeUnit.SECONDS)
        .withSession { sessions }
        .apply {
            if (BuildConfig.DEBUG) {
                // BASIC logs method, URL and status only; the header is redacted in case the level
                // is raised while debugging.
                val logging = HttpLoggingInterceptor().setLevel(HttpLoggingInterceptor.Level.BASIC)
                logging.redactHeader("Authorization")
                addInterceptor(logging)
            }
        }
        .build()

    private val api: Series76Api = Retrofit.Builder()
        .baseUrl(baseUrl.trimEnd('/') + "/")
        .client(http)
        .addConverterFactory(ApiJson.asConverterFactory("application/json".toMediaType()))
        .build()
        .create(Series76Api::class.java)

    private val call = ApiCall(ApiJson, AndroidConnectivityChecker(context))

    val sessionManager: SessionManager =
        SessionManager(NetworkAuthApi(api, call), TinkSessionStore(context.applicationContext), appScope)
            .also { sessions = it }

    val repository: ConversationRepository = NetworkConversationRepository(api, call)

    val debugRepository: DebugRepository = NetworkDebugRepository(api, call)
}

/** Adds the Bearer token of [sessions] and reports 401s to it. Shared with the wiring test. */
fun OkHttpClient.Builder.withSession(sessions: () -> SessionManager): OkHttpClient.Builder =
    addInterceptor(
        AuthInterceptor(token = { sessions().token }, onUnauthorized = { sessions().onUnauthorized(it) }),
    )
