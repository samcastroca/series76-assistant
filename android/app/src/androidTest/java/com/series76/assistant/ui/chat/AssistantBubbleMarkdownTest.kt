package com.series76.assistant.ui.chat

import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.platform.UriHandler
import androidx.compose.ui.test.ExperimentalTestApi
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.series76.assistant.data.api.model.ResponseType
import com.series76.assistant.ui.chat.components.AssistantBubble
import com.series76.assistant.ui.theme.Series76AssistantTheme
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Regression: the model answers in Markdown and the bubble showed it raw ("**...**", "- ...").
 * The answer must be rendered: the markers disappear and the text stays. Links the model
 * writes must not open anything.
 */
@RunWith(AndroidJUnit4::class)
class AssistantBubbleMarkdownTest {
    @get:Rule val compose = createComposeRule()

    /** Every URI the app tries to open from outside the bubble. */
    private val opened = mutableListOf<String>()

    private fun show(answer: String) = compose.setContent {
        val recorder = object : UriHandler {
            override fun openUri(uri: String) {
                opened += uri
            }
        }
        CompositionLocalProvider(LocalUriHandler provides recorder) { Bubble(answer) }
    }

    @Composable
    private fun Bubble(answer: String) {
        Series76AssistantTheme {
            AssistantBubble(
                item = ChatItem.Assistant(
                    id = "a1",
                    answer = answer,
                    responseType = ResponseType.DETAIL,
                    products = emptyList(),
                    clarification = null,
                    suggestions = emptyList(),
                    warnings = emptyList(),
                ),
                chipsEnabled = true,
                onChip = {},
            )
        }
    }

    /** The renderer parses Markdown off the main thread: wait until [text] is on screen. */
    @OptIn(ExperimentalTestApi::class)
    private fun awaitText(text: String) =
        compose.waitUntilAtLeastOneExists(hasText(text, substring = true), timeoutMillis = 5_000)

    @Test
    fun linksWrittenByTheModelDoNotOpen() {
        show("[este sitio](https://example.com)") // the whole node is the link
        awaitText("este sitio")
        compose.onNodeWithText("este sitio", substring = true).performClick()
        compose.waitForIdle()
        assertTrue("opened: $opened", opened.isEmpty())
    }

    @Test
    fun boldIsRenderedWithoutAsterisks() {
        show("El modelo **recomendado** cumple.")
        awaitText("recomendado")
        compose.onNodeWithText("**", substring = true).assertDoesNotExist()
    }

    @Test
    fun listItemsAreRenderedWithoutDashes() {
        show("Opciones:\n\n- Weatherproof\n- Explosionproof")
        awaitText("Explosionproof") // the last item: the whole list is rendered
        compose.onNodeWithText("- Weatherproof", substring = true).assertDoesNotExist()
    }
}
