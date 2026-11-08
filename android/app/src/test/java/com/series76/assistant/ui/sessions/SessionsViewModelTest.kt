package com.series76.assistant.ui.sessions

import com.series76.assistant.data.ApiResult
import com.series76.assistant.data.AppError
import com.series76.assistant.testutil.FakeConversationRepository
import com.series76.assistant.testutil.Fixtures
import com.series76.assistant.testutil.MainDispatcherRule
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

class SessionsViewModelTest {
    @get:Rule val main = MainDispatcherRule()

    private val repo = FakeConversationRepository()
    private val vm = SessionsViewModel(repo)

    private fun content() = vm.state.value as SessionsUiState.Content

    @Test
    fun `first show loads the list from Loading to Content`() = runTest(main.dispatcher) {
        vm.onShown()
        assertEquals(SessionsUiState.Loading, vm.state.value)
        advanceUntilIdle()
        assertEquals(Fixtures.sessions(), content().sessions)
    }

    @Test
    fun `each failure shows its error and Retry loads again`() = runTest(main.dispatcher) {
        val errors = listOf(AppError.Timeout, AppError.Server(500), AppError.BudgetExceeded, AppError.Offline)
        for (error in errors) {
            repo.sessionsResults.addLast(ApiResult.Err(error))
            vm.load()
            advanceUntilIdle()
            assertEquals(SessionsUiState.Error(error), vm.state.value)
            assertTrue(error.retryable)
        }
        vm.load() // "Retry"
        advanceUntilIdle()
        assertEquals(Fixtures.sessions(), content().sessions)
    }

    @Test
    fun `coming back to the screen refreshes without hiding the list`() = runTest(main.dispatcher) {
        vm.onShown()
        advanceUntilIdle()
        repo.sessionsResults.addLast(ApiResult.Ok(Fixtures.sessions().drop(1)))

        vm.onShown()
        assertTrue(content().isRefreshing)
        assertEquals(Fixtures.sessions(), content().sessions) // still visible while refreshing
        advanceUntilIdle()
        assertEquals(Fixtures.sessions().drop(1), content().sessions)
        assertEquals(2, repo.sessionsCalls)
    }

    @Test
    fun `a failed refresh keeps the list and offers Retry`() = runTest(main.dispatcher) {
        vm.onShown()
        advanceUntilIdle()
        repo.sessionsResults.addLast(ApiResult.Err(AppError.Unreachable))

        vm.refresh()
        advanceUntilIdle()
        assertEquals(Fixtures.sessions(), content().sessions)
        assertEquals(TransientError(AppError.Unreachable, FailedAction.REFRESH), content().transientError)

        vm.retryTransient()
        advanceUntilIdle()
        assertNull(content().transientError)
        assertEquals(3, repo.sessionsCalls)
    }

    @Test
    fun `delete waits for confirmation`() = runTest(main.dispatcher) {
        vm.onShown()
        advanceUntilIdle()
        val target = Fixtures.sessions().first()

        vm.requestDelete(target)
        assertEquals(target, content().pendingDelete)
        vm.cancelDelete()
        advanceUntilIdle()
        assertNull(content().pendingDelete)
        assertTrue(repo.deleted.isEmpty())

        vm.requestDelete(target)
        vm.confirmDelete()
        advanceUntilIdle()
        assertEquals(listOf(target.id), repo.deleted)
        assertTrue(content().sessions.none { it.id == target.id })
    }

    @Test
    fun `a failed delete keeps the row and Retry deletes it`() = runTest(main.dispatcher) {
        vm.onShown()
        advanceUntilIdle()
        val target = Fixtures.sessions().first()
        repo.deleteResults.addLast(ApiResult.Err(AppError.Timeout))

        vm.requestDelete(target)
        vm.confirmDelete()
        advanceUntilIdle()
        assertTrue(content().sessions.any { it.id == target.id })
        assertEquals(TransientError(AppError.Timeout, FailedAction.DELETE), content().transientError)

        vm.retryTransient()
        advanceUntilIdle()
        assertEquals(listOf(target.id, target.id), repo.deleted)
        assertTrue(content().sessions.none { it.id == target.id })
    }

    @Test
    fun `a refresh that started before a delete does not bring the row back`() = runTest(main.dispatcher) {
        vm.onShown()
        advanceUntilIdle()
        val target = Fixtures.sessions().first()

        val gate = CompletableDeferred<Unit>()
        repo.sessionsGate = gate
        vm.refresh() // waits at the gate; its result still lists the target
        runCurrent()
        vm.requestDelete(target)
        vm.confirmDelete()
        runCurrent()
        assertTrue(content().sessions.none { it.id == target.id }) // the delete finished first

        gate.complete(Unit)
        advanceUntilIdle()
        assertTrue(content().sessions.none { it.id == target.id })
    }

    @Test
    fun `deleting a conversation that is already gone removes the row`() = runTest(main.dispatcher) {
        vm.onShown()
        advanceUntilIdle()
        val target = Fixtures.sessions().first()
        repo.deleteResults.addLast(ApiResult.Err(AppError.Client(404, "session_not_found")))

        vm.requestDelete(target)
        vm.confirmDelete()
        advanceUntilIdle()
        assertTrue(content().sessions.none { it.id == target.id })
        assertNull(content().transientError)
    }
}
