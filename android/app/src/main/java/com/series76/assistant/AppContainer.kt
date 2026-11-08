package com.series76.assistant

import android.content.Context
import com.series76.assistant.data.AndroidConnectivityChecker
import com.series76.assistant.data.ConversationRepository
import com.series76.assistant.data.NetworkConversationRepository
import com.series76.assistant.data.api.ApiJson
import com.series76.assistant.data.api.Series76Api
import java.util.concurrent.TimeUnit
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Retrofit
import retrofit2.converter.kotlinx.serialization.asConverterFactory

/** Manual dependency injection: the app-wide objects, created once in [Series76App]. */
class AppContainer(context: Context, baseUrl: String = BuildConfig.API_BASE_URL) {

    private val http = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        // A chat turn runs the agent (model calls, tools, one validator retry): allow for it.
        .readTimeout(90, TimeUnit.SECONDS)
        .apply {
            if (BuildConfig.DEBUG) {
                addInterceptor(HttpLoggingInterceptor().setLevel(HttpLoggingInterceptor.Level.BASIC))
            }
        }
        .build()

    private val api: Series76Api = Retrofit.Builder()
        .baseUrl(baseUrl.trimEnd('/') + "/")
        .client(http)
        .addConverterFactory(ApiJson.asConverterFactory("application/json".toMediaType()))
        .build()
        .create(Series76Api::class.java)

    val repository: ConversationRepository =
        NetworkConversationRepository(api, ApiJson, AndroidConnectivityChecker(context))
}
