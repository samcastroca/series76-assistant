package com.series76.assistant.ui.chat

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.series76.assistant.R
import com.series76.assistant.data.AppError
import com.series76.assistant.data.api.model.MessageOut
import com.series76.assistant.ui.chat.components.AssistantBubble
import com.series76.assistant.ui.chat.components.TypingBubble
import com.series76.assistant.ui.chat.components.UserBubble
import com.series76.assistant.ui.common.ErrorState
import com.series76.assistant.ui.preview.PreviewFixtures
import com.series76.assistant.ui.theme.Series76AssistantTheme

@Composable
fun ChatRoute(viewModel: ChatViewModel, title: String?, onBack: () -> Unit) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    ChatScreen(
        state = state,
        title = title ?: stringResource(R.string.new_conversation),
        onBack = onBack,
        onInputChange = viewModel::onInputChange,
        onSend = viewModel::send,
        onChip = viewModel::sendChip,
        onRetrySend = viewModel::retry,
        onRetryLoad = viewModel::loadHistory,
    )
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ChatScreen(
    state: ChatUiState,
    title: String,
    onBack: () -> Unit,
    onInputChange: (String) -> Unit,
    onSend: () -> Unit,
    onChip: (String) -> Unit,
    onRetrySend: (String) -> Unit,
    onRetryLoad: () -> Unit,
) {
    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(title, maxLines = 1, overflow = TextOverflow.Ellipsis) },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, stringResource(R.string.back))
                    }
                },
            )
        },
    ) { padding ->
        Box(Modifier.padding(padding).fillMaxSize().imePadding()) {
            when (state) {
                ChatUiState.Loading -> CircularProgressIndicator(Modifier.align(Alignment.Center))
                is ChatUiState.Error -> ErrorState(
                    title = stringResource(R.string.chat_load_error),
                    error = state.error,
                    onRetry = onRetryLoad,
                )
                is ChatUiState.Content -> ChatContent(state, onInputChange, onSend, onChip, onRetrySend)
            }
        }
    }
}

@Composable
private fun ChatContent(
    state: ChatUiState.Content,
    onInputChange: (String) -> Unit,
    onSend: () -> Unit,
    onChip: (String) -> Unit,
    onRetrySend: (String) -> Unit,
) {
    val listState = rememberLazyListState()
    LaunchedEffect(state.items.size, state.isSending) {
        val last = state.items.size + if (state.isSending) 1 else 0
        if (last > 0) listState.animateScrollToItem(last - 1)
    }
    Column(Modifier.fillMaxSize()) {
        if (state.offline) OfflineBanner()
        Box(Modifier.weight(1f)) {
            if (state.items.isEmpty() && !state.isSending) {
                Text(
                    stringResource(R.string.chat_empty),
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    textAlign = TextAlign.Center,
                    modifier = Modifier.align(Alignment.Center).padding(32.dp),
                )
            }
            LazyColumn(
                state = listState,
                contentPadding = PaddingValues(12.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
                modifier = Modifier.fillMaxSize(),
            ) {
                val lastId = state.items.lastOrNull()?.id
                items(state.items, key = { it.id }) { item ->
                    when (item) {
                        is ChatItem.User -> UserBubble(
                            item = item,
                            canRetry = item.id == lastId && !state.isSending,
                            onRetry = { onRetrySend(item.id) },
                        )
                        is ChatItem.Assistant -> AssistantBubble(
                            item = item,
                            chipsEnabled = !state.isSending,
                            onChip = onChip,
                        )
                    }
                }
                if (state.isSending) item(key = "typing") { TypingBubble() }
            }
        }
        InputBar(state, onInputChange, onSend)
    }
}

@Composable
private fun OfflineBanner() {
    Text(
        stringResource(R.string.offline_banner),
        color = MaterialTheme.colorScheme.onErrorContainer,
        style = MaterialTheme.typography.labelLarge,
        textAlign = TextAlign.Center,
        modifier = Modifier
            .fillMaxWidth()
            .background(MaterialTheme.colorScheme.errorContainer)
            .padding(vertical = 6.dp),
    )
}

@Composable
private fun InputBar(state: ChatUiState.Content, onInputChange: (String) -> Unit, onSend: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().padding(8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        OutlinedTextField(
            value = state.input,
            onValueChange = onInputChange,
            placeholder = { Text(stringResource(R.string.chat_input_hint)) },
            maxLines = 4,
            modifier = Modifier.weight(1f),
        )
        IconButton(onClick = onSend, enabled = state.canSend) {
            Icon(Icons.AutoMirrored.Filled.Send, stringResource(R.string.send))
        }
    }
}

// --- Previews (fed by the generated fixtures) ---------------------------------------------------

@Composable
private fun historyState(): ChatUiState.Content {
    val messages = PreviewFixtures.load<List<MessageOut>>("messages")
    return ChatUiState.Content(items = messages.map { it.toChatItem() })
}

@Preview(showBackground = true, heightDp = 900)
@Composable
private fun ChatHistoryPreview() {
    Series76AssistantTheme {
        ChatScreen(historyState(), "Conversación", {}, {}, {}, {}, {}, {})
    }
}

@Preview(showBackground = true)
@Composable
private fun ChatSendingPreview() {
    val state = historyState().let { it.copy(items = it.items.take(1), isSending = true) }
    Series76AssistantTheme { ChatScreen(state, "Conversación", {}, {}, {}, {}, {}, {}) }
}

@Preview(showBackground = true)
@Composable
private fun ChatOfflinePreview() {
    val state = ChatUiState.Content(
        items = listOf(ChatItem.User("1", "¿Qué opciones hay?", failed = true, error = AppError.Offline)),
        offline = true,
    )
    Series76AssistantTheme { ChatScreen(state, "Nueva conversación", {}, {}, {}, {}, {}, {}) }
}

@Preview(showBackground = true)
@Composable
private fun ChatLoadErrorPreview() {
    Series76AssistantTheme {
        ChatScreen(ChatUiState.Error(AppError.Unreachable), "Conversación", {}, {}, {}, {}, {}, {})
    }
}
