package com.series76.assistant.ui.auth

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.series76.assistant.R
import com.series76.assistant.data.AppError
import com.series76.assistant.data.api.model.UserOut
import com.series76.assistant.ui.common.errorText
import com.series76.assistant.ui.preview.PreviewFixtures
import com.series76.assistant.ui.theme.Series76AssistantTheme

@Composable
fun LoginRoute(viewModel: LoginViewModel) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    LoginScreen(
        state = state,
        onEmailChange = viewModel::onEmailChange,
        onPasswordChange = viewModel::onPasswordChange,
        onTogglePassword = viewModel::togglePasswordVisible,
        onSubmit = viewModel::submit,
    )
}

@Composable
fun LoginScreen(
    state: LoginUiState,
    onEmailChange: (String) -> Unit,
    onPasswordChange: (String) -> Unit,
    onTogglePassword: () -> Unit,
    onSubmit: () -> Unit,
) {
    Scaffold { padding ->
        Column(
            modifier = Modifier
                .padding(padding)
                .fillMaxSize()
                .imePadding()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 24.dp, vertical = 32.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp, Alignment.CenterVertically),
        ) {
            Text(stringResource(R.string.login_title), style = MaterialTheme.typography.headlineMedium)
            Text(
                stringResource(R.string.login_subtitle),
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            OutlinedTextField(
                value = state.email,
                onValueChange = onEmailChange,
                label = { Text(stringResource(R.string.email)) },
                singleLine = true,
                enabled = !state.loading,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Email, imeAction = ImeAction.Next),
                modifier = Modifier.fillMaxWidth(),
            )
            OutlinedTextField(
                value = state.password,
                onValueChange = onPasswordChange,
                label = { Text(stringResource(R.string.password)) },
                singleLine = true,
                enabled = !state.loading,
                isError = state.error != null,
                visualTransformation =
                    if (state.passwordVisible) VisualTransformation.None else PasswordVisualTransformation(),
                trailingIcon = {
                    IconButton(onClick = onTogglePassword) {
                        Icon(
                            painterResource(
                                if (state.passwordVisible) R.drawable.ic_visibility_off else R.drawable.ic_visibility,
                            ),
                            contentDescription = stringResource(
                                if (state.passwordVisible) R.string.hide_password else R.string.show_password,
                            ),
                        )
                    }
                },
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password, imeAction = ImeAction.Done),
                keyboardActions = KeyboardActions(onDone = { onSubmit() }),
                modifier = Modifier.fillMaxWidth(),
            )
            state.error?.let {
                Text(
                    errorText(it),
                    color = MaterialTheme.colorScheme.error,
                    style = MaterialTheme.typography.bodyMedium,
                    modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite },
                )
            }
            Button(onClick = onSubmit, enabled = state.canSubmit, modifier = Modifier.fillMaxWidth()) {
                if (state.loading) {
                    CircularProgressIndicator(modifier = Modifier.size(20.dp), strokeWidth = 2.dp)
                } else {
                    Text(stringResource(R.string.sign_in))
                }
            }
        }
    }
}

// --- Previews ----------------------------------------------------------------------------------

@Composable
private fun PreviewLogin(state: LoginUiState) {
    Series76AssistantTheme { LoginScreen(state, {}, {}, {}, {}) }
}

/** The demo user from the generated `auth_me` fixture. */
@Composable
private fun fixtureEmail(): String = PreviewFixtures.load<UserOut>("auth_me").email

@Preview(showBackground = true)
@Composable
private fun LoginEmptyPreview() = PreviewLogin(LoginUiState())

@Preview(showBackground = true)
@Composable
private fun LoginLoadingPreview() =
    PreviewLogin(LoginUiState(email = fixtureEmail(), password = "secret", loading = true))

@Preview(showBackground = true)
@Composable
private fun LoginInvalidCredentialsPreview() = PreviewLogin(
    LoginUiState(
        email = fixtureEmail(),
        password = "secret",
        error = AppError.Client(401, "invalid_credentials"),
    ),
)

@Preview(showBackground = true)
@Composable
private fun LoginOfflinePreview() =
    PreviewLogin(LoginUiState(email = fixtureEmail(), password = "secret", error = AppError.Offline))
