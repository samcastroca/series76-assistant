package com.series76.assistant.ui.chat

import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onAllNodesWithContentDescription
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.series76.assistant.R
import com.series76.assistant.data.api.ApiJson
import com.series76.assistant.data.api.model.MessageOut
import com.series76.assistant.data.api.model.Role
import com.series76.assistant.ui.theme.Series76AssistantTheme
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * The Debug entries (the bug icon on each answer and the ⋮ → Debug menu) exist only for a
 * developer. For an engineer they are not composed at all, not merely disabled.
 */
@RunWith(AndroidJUnit4::class)
class ChatScreenDebugAccessTest {
    @get:Rule val compose = createComposeRule()

    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private val messages: List<MessageOut> =
        ApiJson.decodeFromString(context.assets.open("messages.json").bufferedReader().use { it.readText() })
    // One question and its answer, so every bubble is on screen (the list is lazy).
    private val answer = messages.first { it.role == Role.ASSISTANT }
    private val question = messages.last { it.role == Role.USER && it.createdAt <= answer.createdAt }
    private val state = ChatUiState.Content(items = listOf(question, answer).map { it.toChatItem() })
    private val openTrace = context.getString(R.string.debug_open_trace)
    private val moreOptions = context.getString(R.string.more_options)

    private fun show(onOpenDebug: ((String?) -> Unit)?) = compose.setContent {
        Series76AssistantTheme {
            ChatScreen(state, "Conversación", {}, {}, {}, {}, {}, {}, onOpenDebug = onOpenDebug)
        }
    }

    @Test
    fun engineerSeesNoDebugEntry() {
        show(onOpenDebug = null)
        compose.onNodeWithText(question.content).assertIsDisplayed() // the conversation is on screen...
        compose.onAllNodesWithContentDescription(openTrace).assertCountEquals(0) // ...without a bug icon
        compose.onAllNodesWithContentDescription(moreOptions).assertCountEquals(0)
    }

    @Test
    fun developerOpensTheTraceOfAnAnswerAndOfTheConversation() {
        val opened = mutableListOf<String?>()
        show(onOpenDebug = { opened += it })

        compose.onNodeWithContentDescription(openTrace).performClick()
        compose.onNodeWithContentDescription(moreOptions).performClick()
        compose.onNodeWithText(context.getString(R.string.debug_title)).performClick()

        assertEquals(listOf(answer.id, null), opened)
    }
}
