package com.series76.assistant.ui.preview

import androidx.compose.runtime.Composable
import androidx.compose.ui.platform.LocalContext
import com.series76.assistant.data.api.ApiJson

/**
 * Reads a generated JSON fixture (android/app/src/test/resources, exposed as debug assets) for
 * a @Preview, so previews show real API payloads instead of hand-typed catalog values.
 */
object PreviewFixtures {
    @Composable
    inline fun <reified T> load(name: String): T {
        val text = LocalContext.current.assets.open("$name.json").bufferedReader().use { it.readText() }
        return ApiJson.decodeFromString<T>(text)
    }
}
