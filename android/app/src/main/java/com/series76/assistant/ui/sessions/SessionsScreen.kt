package com.series76.assistant.ui.sessions

import android.text.format.DateUtils
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AccountCircle
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExtendedFloatingActionButton
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.ListItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarDuration
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.SnackbarResult
import androidx.compose.material3.SwipeToDismissBox
import androidx.compose.material3.SwipeToDismissBoxValue
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.material3.rememberSwipeToDismissBoxState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.series76.assistant.R
import com.series76.assistant.data.AppError
import com.series76.assistant.data.api.model.SessionSummary
import com.series76.assistant.data.api.model.UserOut
import com.series76.assistant.ui.common.ErrorState
import com.series76.assistant.ui.common.errorText
import com.series76.assistant.ui.preview.PreviewFixtures
import com.series76.assistant.ui.theme.Series76AssistantTheme
import kotlinx.coroutines.launch

@Composable
fun SessionsRoute(
    viewModel: SessionsViewModel,
    user: UserOut,
    onSignOut: () -> Unit,
    onOpen: (SessionSummary) -> Unit,
    onNew: () -> Unit,
) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    // Runs on first open and again when coming back from a chat (the list may have changed).
    LaunchedEffect(Unit) { viewModel.onShown() }
    SessionsScreen(
        state = state,
        user = user,
        onSignOut = onSignOut,
        onOpen = onOpen,
        onNew = onNew,
        onRetry = viewModel::load,
        onRefresh = viewModel::refresh,
        onRequestDelete = viewModel::requestDelete,
        onConfirmDelete = viewModel::confirmDelete,
        onCancelDelete = viewModel::cancelDelete,
        onRetryTransient = viewModel::retryTransient,
        onTransientShown = viewModel::transientErrorShown,
    )
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SessionsScreen(
    state: SessionsUiState,
    user: UserOut,
    onSignOut: () -> Unit,
    onOpen: (SessionSummary) -> Unit,
    onNew: () -> Unit,
    onRetry: () -> Unit,
    onRefresh: () -> Unit,
    onRequestDelete: (SessionSummary) -> Unit,
    onConfirmDelete: () -> Unit,
    onCancelDelete: () -> Unit,
    onRetryTransient: () -> Unit,
    onTransientShown: () -> Unit,
) {
    val snackbar = remember { SnackbarHostState() }
    val transient = (state as? SessionsUiState.Content)?.transientError
    if (transient != null) {
        val message = stringResource(
            if (transient.action == FailedAction.DELETE) R.string.delete_failed else R.string.refresh_failed,
        ) + " " + errorText(transient.error)
        val retry = stringResource(R.string.retry)
        LaunchedEffect(transient) {
            val result = snackbar.showSnackbar(
                message,
                actionLabel = retry.takeIf { transient.error.retryable },
                duration = SnackbarDuration.Long,
            )
            if (result == SnackbarResult.ActionPerformed) onRetryTransient() else onTransientShown()
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(stringResource(R.string.sessions_title)) },
                actions = { AccountMenu(user, onSignOut) },
            )
        },
        floatingActionButton = {
            ExtendedFloatingActionButton(
                onClick = onNew,
                icon = { Icon(Icons.Filled.Add, contentDescription = null) },
                text = { Text(stringResource(R.string.new_conversation)) },
            )
        },
        snackbarHost = { SnackbarHost(snackbar) },
    ) { padding ->
        Box(Modifier.padding(padding).fillMaxSize()) {
            when (state) {
                SessionsUiState.Loading -> SessionsSkeleton()
                is SessionsUiState.Error -> ErrorState(
                    title = stringResource(R.string.sessions_load_error),
                    error = state.error,
                    onRetry = onRetry,
                )
                is SessionsUiState.Content -> PullToRefreshBox(
                    isRefreshing = state.isRefreshing,
                    onRefresh = onRefresh,
                    modifier = Modifier.fillMaxSize(),
                ) {
                    if (state.sessions.isEmpty()) {
                        EmptySessions(onNew)
                    } else {
                        SessionList(state.sessions, onOpen, onRequestDelete)
                    }
                }
            }
        }
    }

    val pending = (state as? SessionsUiState.Content)?.pendingDelete
    if (pending != null) {
        AlertDialog(
            onDismissRequest = onCancelDelete,
            title = { Text(stringResource(R.string.delete_dialog_title)) },
            text = { Text(stringResource(R.string.delete_dialog_body, pending.title)) },
            confirmButton = { TextButton(onClick = onConfirmDelete) { Text(stringResource(R.string.delete)) } },
            dismissButton = { TextButton(onClick = onCancelDelete) { Text(stringResource(R.string.cancel)) } },
        )
    }
}

@Composable
private fun SessionList(
    sessions: List<SessionSummary>,
    onOpen: (SessionSummary) -> Unit,
    onRequestDelete: (SessionSummary) -> Unit,
) {
    LazyColumn(Modifier.fillMaxSize()) {
        items(sessions, key = { it.id }) { session ->
            SwipeToDeleteRow(onDelete = { onRequestDelete(session) }) {
                SessionRow(session, onClick = { onOpen(session) })
            }
            HorizontalDivider()
        }
    }
}

/** Swiping asks for confirmation instead of deleting: the row always springs back. */
@Composable
private fun SwipeToDeleteRow(onDelete: () -> Unit, content: @Composable () -> Unit) {
    val state = rememberSwipeToDismissBoxState()
    val scope = rememberCoroutineScope()
    SwipeToDismissBox(
        state = state,
        enableDismissFromStartToEnd = false,
        onDismiss = { value ->
            if (value == SwipeToDismissBoxValue.EndToStart) onDelete()
            scope.launch { state.reset() }
        },
        backgroundContent = {
            Box(
                Modifier.fillMaxSize().background(MaterialTheme.colorScheme.errorContainer).padding(horizontal = 20.dp),
                contentAlignment = Alignment.CenterEnd,
            ) {
                Icon(Icons.Filled.Delete, stringResource(R.string.delete), tint = MaterialTheme.colorScheme.onErrorContainer)
            }
        },
    ) { content() }
}

@Composable
private fun SessionRow(session: SessionSummary, onClick: () -> Unit) {
    val updated = DateUtils.getRelativeTimeSpanString(
        session.updatedAt.toEpochMilli(),
        System.currentTimeMillis(),
        DateUtils.MINUTE_IN_MILLIS,
    ).toString()
    ListItem(
        headlineContent = { Text(session.title, maxLines = 1, overflow = TextOverflow.Ellipsis) },
        supportingContent = {
            Text(pluralStringResource(R.plurals.message_count, session.messageCount, session.messageCount))
        },
        trailingContent = { Text(updated, style = MaterialTheme.typography.labelSmall) },
        modifier = Modifier.clickable(onClick = onClick),
    )
}

@Composable
private fun EmptySessions(onNew: () -> Unit) {
    // Scrollable so pull to refresh also works on the empty state.
    LazyColumn(Modifier.fillMaxSize()) {
        item {
            Column(
                Modifier.fillMaxWidth().padding(32.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                Spacer(Modifier.height(64.dp))
                Text(stringResource(R.string.sessions_empty_title), style = MaterialTheme.typography.titleMedium)
                Text(
                    stringResource(R.string.sessions_empty_body),
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    textAlign = TextAlign.Center,
                )
                Button(onClick = onNew) { Text(stringResource(R.string.new_conversation)) }
            }
        }
    }
}

@Composable
private fun SessionsSkeleton() {
    Column(Modifier.fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.spacedBy(20.dp)) {
        repeat(6) {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                SkeletonBar(0.7f, 16)
                SkeletonBar(0.3f, 12)
            }
        }
    }
}

@Composable
private fun SkeletonBar(widthFraction: Float, heightDp: Int) {
    Box(
        Modifier
            .fillMaxWidth(widthFraction)
            .height(heightDp.dp)
            .background(MaterialTheme.colorScheme.surfaceVariant, MaterialTheme.shapes.small),
    )
}

/** The signed-in user's email and "Sign out". */
@Composable
private fun AccountMenu(user: UserOut, onSignOut: () -> Unit) {
    var expanded by remember { mutableStateOf(false) }
    IconButton(onClick = { expanded = true }) {
        Icon(Icons.Filled.AccountCircle, contentDescription = stringResource(R.string.account_menu))
    }
    DropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
        DropdownMenuItem(text = { Text(user.email) }, onClick = {}, enabled = false)
        HorizontalDivider()
        DropdownMenuItem(
            text = { Text(stringResource(R.string.sign_out)) },
            onClick = {
                expanded = false
                onSignOut()
            },
        )
    }
}

// --- Previews ----------------------------------------------------------------------------------

@Composable
private fun PreviewScreen(state: SessionsUiState) {
    val user: UserOut = PreviewFixtures.load("auth_me")
    Series76AssistantTheme { SessionsScreen(state, user, {}, {}, {}, {}, {}, {}, {}, {}, {}, {}) }
}

@Preview(showBackground = true)
@Composable
private fun SessionsContentPreview() {
    PreviewScreen(SessionsUiState.Content(PreviewFixtures.load("sessions")))
}

@Preview(showBackground = true)
@Composable
private fun SessionsEmptyPreview() = PreviewScreen(SessionsUiState.Content(emptyList()))

@Preview(showBackground = true)
@Composable
private fun SessionsLoadingPreview() = PreviewScreen(SessionsUiState.Loading)

@Preview(showBackground = true)
@Composable
private fun SessionsErrorPreview() = PreviewScreen(SessionsUiState.Error(AppError.Offline))
