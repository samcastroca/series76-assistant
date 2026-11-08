package com.series76.assistant.ui.chat

import com.series76.assistant.data.ApiResult
import com.series76.assistant.data.AppError
import com.series76.assistant.data.api.model.MessageStatus
import com.series76.assistant.data.api.model.Role
import com.series76.assistant.testutil.FakeConversationRepository
import com.series76.assistant.testutil.Fixtures
import com.series76.assistant.testutil.MainDispatcherRule
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

class ChatViewModelTest {
    @get:Rule val main = MainDispatcherRule()

    private val repo = FakeConversationRepository()
    private var ids = 0

    private fun newChat() = ChatViewModel(null, repo) { "local-${++ids}" }

    private fun ChatViewModel.content() = state.value as ChatUiState.Content

    private fun ChatViewModel.type(text: String) {
        onInputChange(text)
        send()
    }

    // --- History ---------------------------------------------------------------------------------

    @Test
    fun `opening a conversation loads its history and rebuilds the cards from products`() =
        runTest(main.dispatcher) {
            val vm = ChatViewModel("s1", repo)
            assertEquals(ChatUiState.Loading, vm.state.value)
            advanceUntilIdle()

            val messages = Fixtures.messages()
            val items = vm.content().items
            assertEquals(messages.map { it.id }, items.map { it.id })
            messages.zip(items).forEach { (message, item) ->
                when (message.role) {
                    Role.USER -> assertEquals(message.content, (item as ChatItem.User).text)
                    Role.ASSISTANT -> {
                        item as ChatItem.Assistant
                        assertEquals(message.content, item.answer)
                        assertEquals(message.responseType, item.responseType)
                        assertEquals(message.products, item.products) // the cards' data
                    }
                }
            }
            assertTrue(items.filterIsInstance<ChatItem.Assistant>().any { it.products.isNotEmpty() })
        }

    @Test
    fun `a failed message in the history can be retried in the same session`() =
        runTest(main.dispatcher) {
            val vm = ChatViewModel("s1", repo)
            advanceUntilIdle()
            val failed = vm.content().items.last() as ChatItem.User
            assertEquals(MessageStatus.ERROR, Fixtures.messages().last().status)
            assertTrue(failed.failed)

            vm.retry(failed.id)
            advanceUntilIdle()
            assertEquals(listOf("s1" to failed.text), repo.sent)
            assertFalse((vm.content().items[vm.content().items.size - 2] as ChatItem.User).failed)
            assertTrue(vm.content().items.last() is ChatItem.Assistant)
        }

    @Test
    fun `a failed history load shows the error and Retry loads it again`() = runTest(main.dispatcher) {
        repo.messagesResults.addLast(ApiResult.Err(AppError.Timeout))
        val vm = ChatViewModel("s1", repo)
        advanceUntilIdle()
        assertEquals(ChatUiState.Error(AppError.Timeout), vm.state.value)

        vm.loadHistory()
        advanceUntilIdle()
        assertEquals(Fixtures.messages().size, vm.content().items.size)
    }

    // --- Sending ---------------------------------------------------------------------------------

    @Test
    fun `send shows the user bubble and typing, then the answer with its cards`() =
        runTest(main.dispatcher) {
            val vm = newChat()
            vm.type("  datos  ")

            val sending = vm.content()
            assertTrue(sending.isSending)
            assertEquals("", sending.input)
            assertEquals(listOf(ChatItem.User("local-1", "datos")), sending.items)
            assertFalse(sending.canSend)

            advanceUntilIdle()
            val done = vm.content()
            assertFalse(done.isSending)
            val answer = done.items.last() as ChatItem.Assistant
            assertEquals(Fixtures.conversation("detail").products, answer.products)
        }

    @Test
    fun `the first response sets the session used by the next turns`() = runTest(main.dispatcher) {
        val vm = newChat()
        vm.type("primera")
        advanceUntilIdle()
        vm.type("segunda")
        advanceUntilIdle()

        val sessionId = Fixtures.conversation("detail").sessionId
        assertEquals(listOf(null to "primera", sessionId to "segunda"), repo.sent)
        assertEquals(sessionId, vm.sessionId)
    }

    @Test
    fun `blank input and sends while a turn is in flight are ignored`() = runTest(main.dispatcher) {
        val vm = newChat()
        vm.type("   ")
        assertTrue(repo.sent.isEmpty())

        vm.type("uno")
        vm.type("dos")
        vm.sendChip("tres")
        advanceUntilIdle()
        assertEquals(listOf(null to "uno"), repo.sent)
    }

    @Test
    fun `each failure marks the bubble with its error and Retry resends it`() =
        runTest(main.dispatcher) {
            val failures = listOf(
                AppError.Timeout,
                AppError.Unreachable,
                AppError.Server(500),
                AppError.BudgetExceeded,
                AppError.AssistantUnavailable,
                AppError.Offline,
            )
            for (error in failures) {
                val vm = newChat()
                repo.sent.clear()
                repo.failNextSend(error)
                vm.type("consulta")
                advanceUntilIdle()

                val failed = vm.content().items.single() as ChatItem.User
                assertTrue(failed.failed)
                assertEquals(error, failed.error)
                assertTrue(error.retryable)
                assertEquals(error == AppError.Offline, vm.content().offline)

                vm.retry(failed.id)
                advanceUntilIdle()
                val items = vm.content().items
                assertEquals(2, items.size) // the retried bubble is reused, not duplicated
                assertFalse((items.first() as ChatItem.User).failed)
                assertNull((items.first() as ChatItem.User).error)
                assertFalse(vm.content().offline)
                assertEquals(listOf(null to "consulta", null to "consulta"), repo.sent)
            }
        }

    @Test
    fun `a retry after a failure in an existing session goes to that session`() =
        runTest(main.dispatcher) {
            val vm = newChat()
            vm.type("primera")
            advanceUntilIdle()
            repo.failNextSend(AppError.AssistantUnavailable)
            vm.type("segunda")
            advanceUntilIdle()

            vm.retry((vm.content().items.last() as ChatItem.User).id)
            advanceUntilIdle()
            val sessionId = Fixtures.conversation("detail").sessionId
            assertEquals(listOf(sessionId to "segunda", sessionId to "segunda"), repo.sent.drop(1))
        }

    @Test
    fun `only the last failed message can be retried`() = runTest(main.dispatcher) {
        val vm = newChat()
        repo.failNextSend(AppError.Timeout)
        vm.type("vieja")
        advanceUntilIdle()
        val old = vm.content().items.single()
        vm.type("nueva")
        advanceUntilIdle()

        vm.retry(old.id)
        advanceUntilIdle()
        assertEquals(listOf("vieja", "nueva"), repo.sent.map { it.second })
    }

    @Test
    fun `a chip sends its text as the next query`() = runTest(main.dispatcher) {
        repo.sendResults.addLast(ApiResult.Ok(Fixtures.conversation("clarification")))
        val vm = newChat()
        vm.type("¿Qué opciones hay?")
        advanceUntilIdle()
        val option = (vm.content().items.last() as ChatItem.Assistant).clarification!!.options.first()

        vm.sendChip(option)
        advanceUntilIdle()
        assertEquals(option, repo.sent.last().second)
        assertEquals(option, (vm.content().items[2] as ChatItem.User).text)
    }

    // --- Retry after an ambiguous failure (the backend may have stored the turn) ---------------

    private fun storedTurn(query: String) =
        Fixtures.messages().take(2).let { (user, assistant) -> listOf(user.copy(content = query), assistant) }

    @Test
    fun `a retry after a timeout does not resend when the turn was stored`() = runTest(main.dispatcher) {
        val vm = newChat()
        vm.type("primera")
        advanceUntilIdle()
        repo.failNextSend(AppError.Timeout)
        vm.type("segunda")
        advanceUntilIdle()
        repo.messagesResults.addLast(ApiResult.Ok(storedTurn("segunda")))

        vm.retry((vm.content().items.last() as ChatItem.User).id)
        assertTrue(vm.content().isSending)
        advanceUntilIdle()
        assertEquals(listOf("primera", "segunda"), repo.sent.map { it.second }) // no third send
        assertEquals(storedTurn("segunda").map { it.id }, vm.content().items.map { it.id })
        assertFalse(vm.content().isSending)
    }

    @Test
    fun `a retry after a dropped connection resends when the turn was not stored`() =
        runTest(main.dispatcher) {
            val vm = newChat()
            vm.type("primera")
            advanceUntilIdle()
            repo.failNextSend(AppError.Unreachable)
            vm.type("segunda")
            advanceUntilIdle()
            repo.messagesResults.addLast(ApiResult.Ok(storedTurn("primera")))

            vm.retry((vm.content().items.last() as ChatItem.User).id)
            advanceUntilIdle()
            assertEquals(listOf("primera", "segunda", "segunda"), repo.sent.map { it.second })
            assertTrue(vm.content().items.last() is ChatItem.Assistant)
        }
}
