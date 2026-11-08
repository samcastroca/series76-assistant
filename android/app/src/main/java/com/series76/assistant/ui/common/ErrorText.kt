package com.series76.assistant.ui.common

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import com.series76.assistant.R
import com.series76.assistant.data.AppError
import com.series76.assistant.ui.theme.Series76AssistantTheme

/** The user-facing message for a failure. Fixed texts: no backend message, no internals. */
@Composable
fun errorText(error: AppError): String = when (error) {
    AppError.Offline -> stringResource(R.string.error_offline)
    AppError.Timeout -> stringResource(R.string.error_timeout)
    AppError.Unreachable -> stringResource(R.string.error_unreachable)
    AppError.BudgetExceeded -> stringResource(R.string.error_budget_exceeded)
    AppError.AssistantUnavailable -> stringResource(R.string.error_assistant_unavailable)
    is AppError.Server -> stringResource(R.string.error_server, error.httpStatus)
    is AppError.Client -> when (error.code) {
        "session_not_found" -> stringResource(R.string.error_session_not_found)
        "query_too_long" -> stringResource(R.string.error_query_too_long)
        "empty_query" -> stringResource(R.string.error_empty_query)
        "invalid_credentials" -> stringResource(R.string.error_invalid_credentials)
        "too_many_attempts" -> stringResource(R.string.error_too_many_attempts)
        else -> stringResource(R.string.error_client, error.httpStatus)
    }
    AppError.Unexpected -> stringResource(R.string.error_unexpected)
}

/** Full-screen error: what failed, why, and "Retry" when retrying can help. */
@Composable
fun ErrorState(
    title: String,
    error: AppError,
    onRetry: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier.fillMaxSize().padding(32.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp, Alignment.CenterVertically),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text(title, style = MaterialTheme.typography.titleMedium, textAlign = TextAlign.Center)
        Text(
            errorText(error),
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            textAlign = TextAlign.Center,
        )
        if (error.retryable) {
            Button(onClick = onRetry) { Text(stringResource(R.string.retry)) }
        }
    }
}

@Preview(showBackground = true)
@Composable
private fun ErrorStateBudgetPreview() {
    Series76AssistantTheme {
        ErrorState("No se pudo cargar la conversación.", AppError.BudgetExceeded, onRetry = {})
    }
}

@Preview(showBackground = true)
@Composable
private fun ErrorStateTimeoutPreview() {
    Series76AssistantTheme {
        ErrorState("No se pudieron cargar las conversaciones.", AppError.Timeout, onRetry = {})
    }
}
