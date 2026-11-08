package com.series76.assistant.ui.auth

import com.series76.assistant.data.ApiResult
import com.series76.assistant.data.AppError
import com.series76.assistant.data.auth.AuthState
import com.series76.assistant.data.auth.SessionManager
import com.series76.assistant.data.auth.StoredSession
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
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

class LoginViewModelTest {
    @get:Rule val main = MainDispatcherRule()

    private val api = FakeAuthApi()
    private val store = InMemorySessionStore()

    private fun TestScope.viewModel(): Pair<LoginViewModel, SessionManager> {
        val sessions = SessionManager(api, store, this)
        return LoginViewModel(sessions) to sessions
    }

    private fun LoginViewModel.fill(email: String = "engineer@example.com", password: String = "secret") {
        onEmailChange(email)
        onPasswordChange(password)
    }

    @Test
    fun `successful login stores the token and user, never the password`() = runTest(main.dispatcher) {
        val (vm, sessions) = viewModel()
        vm.fill()
        vm.submit()
        advanceUntilIdle()

        val login = Fixtures.login()
        assertEquals(AuthState.SignedIn(login.user), sessions.state.value)
        assertEquals(StoredSession(login.accessToken, login.user.email, login.user.role), store.session)
        assertEquals(login.accessToken, sessions.token)
        assertEquals(listOf("engineer@example.com" to "secret"), api.logins)
        assertEquals("", vm.state.value.password) // cleared from memory too
        assertFalse(vm.state.value.loading)
    }

    @Test
    fun `the button is disabled while the login is in flight`() = runTest(main.dispatcher) {
        val (vm, _) = viewModel()
        assertFalse(vm.state.value.canSubmit) // empty fields
        vm.fill()
        assertTrue(vm.state.value.canSubmit)

        val gate = CompletableDeferred<Unit>()
        api.loginGate = gate
        vm.submit()
        runCurrent()
        assertTrue(vm.state.value.loading)
        assertFalse(vm.state.value.canSubmit)
        vm.submit() // a second tap while loading sends nothing
        runCurrent()
        assertEquals(1, api.logins.size)

        gate.complete(Unit)
        advanceUntilIdle()
        assertFalse(vm.state.value.loading)
    }

    @Test
    fun `invalid credentials show the error and store nothing`() = runTest(main.dispatcher) {
        val (vm, sessions) = viewModel()
        val error = AppError.Client(401, "invalid_credentials")
        api.loginResults.addLast(ApiResult.Err(error))
        vm.fill()
        vm.submit()
        advanceUntilIdle()

        assertEquals(error, vm.state.value.error)
        assertFalse(vm.state.value.loading)
        assertTrue(vm.state.value.canSubmit) // the user can correct and try again
        assertNull(store.session)
        assertNull(sessions.token)
        assertEquals(AuthState.Checking, sessions.state.value) // not signed in

        vm.onPasswordChange("secret2") // editing clears the error
        assertNull(vm.state.value.error)
    }

    @Test
    fun `no connection and too many attempts show their own errors`() = runTest(main.dispatcher) {
        val (vm, _) = viewModel()
        val errors = listOf(AppError.Offline, AppError.Client(429, "too_many_attempts"), AppError.Unreachable)
        vm.fill()
        for (error in errors) {
            api.loginResults.addLast(ApiResult.Err(error))
            vm.submit()
            advanceUntilIdle()
            assertEquals(error, vm.state.value.error)
        }
        assertNull(store.session)
    }

    @Test
    fun `password visibility toggles`() = runTest(main.dispatcher) {
        val (vm, _) = viewModel()
        assertFalse(vm.state.value.passwordVisible)
        vm.togglePasswordVisible()
        assertTrue(vm.state.value.passwordVisible)
    }
}
