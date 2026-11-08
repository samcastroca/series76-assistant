package com.series76.assistant.ui.auth

import com.series76.assistant.data.ApiResult
import com.series76.assistant.data.AppError
import com.series76.assistant.data.api.model.UserOut
import com.series76.assistant.data.api.model.UserRole
import com.series76.assistant.data.auth.AuthState
import com.series76.assistant.data.auth.SessionManager
import com.series76.assistant.data.auth.SessionStore
import com.series76.assistant.data.auth.StoredSession
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.ViewModelStore
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import com.series76.assistant.testutil.FailingSessionStore
import com.series76.assistant.testutil.FakeAuthApi
import com.series76.assistant.testutil.Fixtures
import com.series76.assistant.testutil.InMemorySessionStore
import com.series76.assistant.testutil.MainDispatcherRule
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

class SessionViewModelTest {
    @get:Rule val main = MainDispatcherRule()

    private val api = FakeAuthApi()
    private val saved = StoredSession("stored-token", "engineer@example.com", UserRole.ENGINEER)

    private fun TestScope.start(store: SessionStore): Pair<SessionViewModel, SessionManager> {
        val sessions = SessionManager(api, store, this)
        return SessionViewModel(sessions) to sessions
    }

    @Test
    fun `without a stored token the app opens on Login`() = runTest(main.dispatcher) {
        val (vm, _) = start(InMemorySessionStore())
        assertEquals(AuthState.Checking, vm.state.value)
        advanceUntilIdle()
        assertEquals(AuthState.SignedOut, vm.state.value)
        assertEquals(0, api.meCalls) // nothing to validate
    }

    @Test
    fun `a stored token is validated with me before entering`() = runTest(main.dispatcher) {
        val store = InMemorySessionStore(saved)
        val promoted = UserOut(saved.email, UserRole.DEVELOPER)
        api.meResults.addLast(ApiResult.Ok(promoted))
        val (vm, sessions) = start(store)
        advanceUntilIdle()

        assertEquals(AuthState.SignedIn(promoted), vm.state.value)
        assertEquals(1, api.meCalls)
        assertEquals("stored-token", sessions.token)
        assertEquals(UserRole.DEVELOPER, store.session?.role) // the server's role wins
    }

    @Test
    fun `an expired stored token clears the session and opens Login`() = runTest(main.dispatcher) {
        val store = InMemorySessionStore(saved)
        api.meResults.addLast(FakeAuthApi.unauthorized("token_expired"))
        val (vm, sessions) = start(store)
        advanceUntilIdle()

        assertEquals(AuthState.SignedOut, vm.state.value)
        assertNull(store.session)
        assertNull(sessions.token)
    }

    @Test
    fun `a token that expires while in use ends the session`() = runTest(main.dispatcher) {
        val store = InMemorySessionStore(saved)
        val (vm, sessions) = start(store)
        advanceUntilIdle()
        assertTrue(vm.state.value is AuthState.SignedIn)

        sessions.onUnauthorized("stored-token") // what AuthInterceptor reports on a 401
        assertEquals(AuthState.SignedOut, vm.state.value)
        advanceUntilIdle()
        assertNull(store.session)
    }

    @Test
    fun `a late 401 for an older token does not sign out the new session`() = runTest(main.dispatcher) {
        val store = InMemorySessionStore(saved)
        val (vm, sessions) = start(store)
        advanceUntilIdle()

        sessions.onUnauthorized("an-older-token")
        advanceUntilIdle()
        assertTrue(vm.state.value is AuthState.SignedIn)
        assertEquals(saved.token, store.session?.token)
    }

    @Test
    fun `validation failing for a network reason offers Retry instead of Login`() = runTest(main.dispatcher) {
        val store = InMemorySessionStore(saved)
        api.meResults.addLast(ApiResult.Err(AppError.Offline))
        val (vm, _) = start(store)
        advanceUntilIdle()

        assertEquals(AuthState.VerifyFailed(AppError.Offline), vm.state.value)
        assertEquals(saved, store.session) // the token is kept: it may still be valid

        vm.retry()
        advanceUntilIdle()
        assertEquals(AuthState.SignedIn(Fixtures.me()), vm.state.value)
    }

    @Test
    fun `logout clears the stored session and opens Login`() = runTest(main.dispatcher) {
        val store = InMemorySessionStore(saved)
        val (vm, sessions) = start(store)
        advanceUntilIdle()

        vm.logout()
        advanceUntilIdle()
        assertEquals(AuthState.SignedOut, vm.state.value)
        assertNull(store.session)
        assertNull(sessions.token)
    }

    @Test
    fun `logout also works from the Retry screen`() = runTest(main.dispatcher) {
        val store = InMemorySessionStore(saved)
        api.meResults.addLast(ApiResult.Err(AppError.Unreachable))
        val (vm, _) = start(store)
        advanceUntilIdle()

        vm.logout()
        advanceUntilIdle()
        assertEquals(AuthState.SignedOut, vm.state.value)
        assertNull(store.session)
    }

    @Test
    fun `starting twice validates once`() = runTest(main.dispatcher) {
        val sessions = SessionManager(api, InMemorySessionStore(saved), this)
        SessionViewModel(sessions)
        SessionViewModel(sessions) // a configuration change creates the ViewModel again
        advanceUntilIdle()
        assertEquals(1, api.meCalls)
    }

    @Test
    fun `validation finishes even if the screen goes away meanwhile`() = runTest(main.dispatcher) {
        val sessions = SessionManager(api, InMemorySessionStore(saved), this)
        val gate = CompletableDeferred<Unit>()
        api.meGate = gate
        val owner = ViewModelStore()
        val factory = viewModelFactory { initializer { SessionViewModel(sessions) } }
        ViewModelProvider.create(owner, factory)[SessionViewModel::class]
        runCurrent()
        owner.clear() // Back pressed during "Checking": the ViewModel is cleared

        gate.complete(Unit)
        advanceUntilIdle()
        assertEquals(AuthState.SignedIn(Fixtures.me()), sessions.state.value)
    }

    @Test
    fun `a 401 for the previous session token does not end the new one`() = runTest(main.dispatcher) {
        val store = InMemorySessionStore()
        val (_, sessions) = start(store)
        advanceUntilIdle()
        api.loginResults.addLast(FakeAuthApi.loginWith("t1"))
        api.loginResults.addLast(FakeAuthApi.loginWith("t2"))

        sessions.login("e@example.com", "pw") // T1
        sessions.logout()
        sessions.login("e@example.com", "pw") // T2
        sessions.onUnauthorized("t1") // a late response from before the sign-out
        advanceUntilIdle()

        assertTrue(sessions.state.value is AuthState.SignedIn)
        assertEquals("t2", sessions.token)
        assertEquals("t2", store.session?.token) // the clean-up of T1 did not wipe T2 from disk
    }

    @Test
    fun `retry is ignored unless validation failed, so a double tap validates once`() = runTest(main.dispatcher) {
        val store = InMemorySessionStore(saved)
        api.meResults.addLast(ApiResult.Err(AppError.Offline))
        val (vm, _) = start(store)
        advanceUntilIdle()

        vm.retry()
        vm.retry()
        advanceUntilIdle()
        assertEquals(2, api.meCalls) // the first validation and one retry
        assertTrue(vm.state.value is AuthState.SignedIn)
    }

    @Test
    fun `a broken store never crashes the app`() = runTest(main.dispatcher) {
        val (vm, sessions) = start(FailingSessionStore())
        advanceUntilIdle()
        assertEquals(AuthState.SignedOut, vm.state.value) // unreadable: like no session

        assertTrue(sessions.login("e@example.com", "pw") is ApiResult.Ok) // save fails: still signed in
        advanceUntilIdle()
        assertTrue(vm.state.value is AuthState.SignedIn)
        vm.logout() // clear fails: still signed out
        advanceUntilIdle()
        assertEquals(AuthState.SignedOut, vm.state.value)
    }
}
