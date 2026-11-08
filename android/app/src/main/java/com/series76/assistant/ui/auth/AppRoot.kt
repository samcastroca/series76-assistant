package com.series76.assistant.ui.auth

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.LocalViewModelStoreOwner
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import com.series76.assistant.R
import com.series76.assistant.data.AppError
import com.series76.assistant.data.ConversationRepository
import com.series76.assistant.data.auth.AuthState
import com.series76.assistant.data.auth.SessionManager
import com.series76.assistant.navigation.AppNavHost
import com.series76.assistant.ui.common.errorText
import com.series76.assistant.ui.theme.Series76AssistantTheme

/**
 * One screen per session state. Signing out (or any 401) moves the state to SignedOut: the
 * signed-in navigation graph leaves the composition and its ViewModel store is cleared
 * ([BranchViewModelStores]), so nothing of the previous session stays in memory or on a back stack.
 */
@Composable
fun AppRoot(sessions: SessionManager, repository: ConversationRepository) {
    val session: SessionViewModel = viewModel(
        factory = viewModelFactory { initializer { SessionViewModel(sessions) } },
    )
    val stores: BranchViewModelStores = viewModel()
    val state by session.state.collectAsStateWithLifecycle()
    val branch = when (val current = state) {
        AuthState.SignedOut -> "signed-out"
        is AuthState.SignedIn -> "signed-in:${current.user.email}"
        else -> "none" // Checking, VerifyFailed: no ViewModels of their own
    }
    LaunchedEffect(branch) { stores.keepOnly(branch) }

    CompositionLocalProvider(LocalViewModelStoreOwner provides stores.owner(branch)) {
        when (val current = state) {
            AuthState.Checking -> CheckingScreen()
            is AuthState.VerifyFailed -> VerifyFailedScreen(current.error, session::retry, session::logout)
            AuthState.SignedOut -> {
                val login: LoginViewModel = viewModel(
                    factory = viewModelFactory { initializer { LoginViewModel(sessions) } },
                )
                LoginRoute(login)
            }
            is AuthState.SignedIn -> AppNavHost(repository, current.user, onSignOut = session::logout)
        }
    }
}

@Composable
private fun CheckingScreen() {
    Scaffold { padding ->
        Column(
            modifier = Modifier.padding(padding).fillMaxSize(),
            verticalArrangement = Arrangement.spacedBy(16.dp, Alignment.CenterVertically),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            CircularProgressIndicator()
            Text(stringResource(R.string.checking_session), style = MaterialTheme.typography.bodyMedium)
        }
    }
}

/** A stored token could not be checked (offline, server down): retry, or sign out and log in. */
@Composable
fun VerifyFailedScreen(error: AppError, onRetry: () -> Unit, onSignOut: () -> Unit) {
    Scaffold { padding ->
        Column(
            modifier = Modifier.padding(padding).fillMaxSize().padding(32.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp, Alignment.CenterVertically),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text(
                stringResource(R.string.verify_failed_title),
                style = MaterialTheme.typography.titleMedium,
                textAlign = TextAlign.Center,
            )
            Text(
                errorText(error),
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                textAlign = TextAlign.Center,
            )
            Button(onClick = onRetry) { Text(stringResource(R.string.retry)) }
            TextButton(onClick = onSignOut) { Text(stringResource(R.string.sign_out)) }
        }
    }
}

@Preview(showBackground = true)
@Composable
private fun VerifyFailedPreview() {
    Series76AssistantTheme { VerifyFailedScreen(AppError.Offline, {}, {}) }
}

@Preview(showBackground = true)
@Composable
private fun CheckingPreview() {
    Series76AssistantTheme { CheckingScreen() }
}
