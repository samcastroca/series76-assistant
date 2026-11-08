package com.series76.assistant.ui.debug

import com.series76.assistant.data.ApiResult
import com.series76.assistant.data.AppError
import com.series76.assistant.data.DebugRepository
import com.series76.assistant.data.api.model.Role
import com.series76.assistant.data.api.model.TraceOut
import com.series76.assistant.testutil.FakeConversationRepository
import com.series76.assistant.testutil.Fixtures
import com.series76.assistant.testutil.MainDispatcherRule
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

class DebugViewModelTest {
    @get:Rule val main = MainDispatcherRule()

    /** Traces from the generated fixture, one per requested message; failures can be queued. */
    private class FakeDebugRepository : DebugRepository {
        val results = ArrayDeque<ApiResult<TraceOut>>()
        val requested = mutableListOf<String>()

        override suspend fun trace(messageId: String): ApiResult<TraceOut> {
            requested += messageId
            return results.removeFirstOrNull() ?: ApiResult.Ok(Fixtures.trace().copy(messageId = messageId))
        }
    }

    private val conversations = FakeConversationRepository()
    private val debug = FakeDebugRepository()
    private val answers = Fixtures.messages().filter { it.role == Role.ASSISTANT }
    private val forbidden = ApiResult.Err(AppError.Client(403, "forbidden"))

    private fun viewModel(focus: String? = null) = DebugViewModel("s1", focus, conversations, debug)

    @Test
    fun `loads the trace of every answer in order and skips the user's messages`() = runTest(main.dispatcher) {
        val vm = viewModel()
        assertEquals(DebugUiState.Loading, vm.state.value)
        advanceUntilIdle()

        val content = vm.state.value as DebugUiState.Content
        assertEquals(answers.map { it.id }, debug.requested)
        assertEquals(answers.map { it.id }, content.entries.map { it.messageId })
        assertEquals(answers.map { it.content }, content.entries.map { it.answer })
        assertNull(content.focusIndex)
    }

    @Test
    fun `opening from a bug icon focuses that answer`() = runTest(main.dispatcher) {
        val target = answers.last()
        val vm = viewModel(focus = target.id)
        advanceUntilIdle()
        assertEquals(answers.lastIndex, (vm.state.value as DebugUiState.Content).focusIndex)
    }

    @Test
    fun `a 403 shows Forbidden, stops asking and is never retried`() = runTest(main.dispatcher) {
        assertTrue("needs two answers to show it stops", answers.size >= 2)
        debug.results.addLast(forbidden)
        val vm = viewModel()
        advanceUntilIdle()

        assertEquals(DebugUiState.Forbidden, vm.state.value)
        assertEquals(1, debug.requested.size) // no request for the other answers

        vm.retry()
        advanceUntilIdle()
        assertEquals(DebugUiState.Forbidden, vm.state.value)
        assertEquals(1, debug.requested.size)
    }

    @Test
    fun `another failure can be retried`() = runTest(main.dispatcher) {
        debug.results.addLast(ApiResult.Err(AppError.Unreachable))
        val vm = viewModel()
        advanceUntilIdle()
        assertEquals(DebugUiState.Error(AppError.Unreachable), vm.state.value)

        vm.retry()
        advanceUntilIdle()
        assertTrue(vm.state.value is DebugUiState.Content)
    }

    @Test
    fun `a failure loading the conversation is an error too`() = runTest(main.dispatcher) {
        conversations.messagesResults.addLast(ApiResult.Err(AppError.Offline))
        val vm = viewModel()
        advanceUntilIdle()
        assertEquals(DebugUiState.Error(AppError.Offline), vm.state.value)
        assertTrue(debug.requested.isEmpty())
    }
}
