package com.series76.assistant.ui.debug

import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.filled.KeyboardArrowUp
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedCard
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.series76.assistant.R
import com.series76.assistant.data.AppError
import com.series76.assistant.data.api.model.MessageOut
import com.series76.assistant.data.api.model.Role
import com.series76.assistant.data.api.model.ToolCallRecord
import com.series76.assistant.data.api.model.TraceOut
import com.series76.assistant.data.api.model.UsageRecord
import com.series76.assistant.ui.common.ErrorState
import com.series76.assistant.ui.preview.PreviewFixtures
import com.series76.assistant.ui.theme.Series76AssistantTheme
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject

private val PrettyJson = Json { prettyPrint = true }

@Composable
fun DebugRoute(viewModel: DebugViewModel, onBack: () -> Unit) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    DebugScreen(state, onBack = onBack, onRetry = viewModel::retry)
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun DebugScreen(state: DebugUiState, onBack: () -> Unit, onRetry: () -> Unit) {
    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(stringResource(R.string.debug_title)) },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, stringResource(R.string.back))
                    }
                },
            )
        },
    ) { padding ->
        Box(Modifier.padding(padding).fillMaxSize()) {
            when (state) {
                DebugUiState.Loading -> CircularProgressIndicator(Modifier.align(Alignment.Center))
                DebugUiState.Forbidden -> CenteredText(stringResource(R.string.debug_forbidden))
                is DebugUiState.Error -> ErrorState(
                    title = stringResource(R.string.debug_load_error),
                    error = state.error,
                    onRetry = onRetry,
                )
                is DebugUiState.Content ->
                    if (state.entries.isEmpty()) {
                        CenteredText(stringResource(R.string.debug_empty))
                    } else {
                        DebugList(state)
                    }
            }
        }
    }
}

@Composable
private fun CenteredText(text: String) {
    Box(Modifier.fillMaxSize().padding(32.dp), contentAlignment = Alignment.Center) {
        Text(text, style = MaterialTheme.typography.titleMedium, textAlign = TextAlign.Center)
    }
}

@Composable
private fun DebugList(state: DebugUiState.Content) {
    // Starts at the focused answer once; after that (rotation included) the user's position stays.
    val list = rememberLazyListState(initialFirstVisibleItemIndex = state.focusIndex ?: 0)
    LazyColumn(
        state = list,
        contentPadding = PaddingValues(12.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        itemsIndexed(state.entries, key = { _, entry -> entry.messageId }) { index, entry ->
            TraceCard(index + 1, entry, expanded = index == state.focusIndex)
        }
    }
}

@Composable
private fun TraceCard(number: Int, entry: DebugEntry, expanded: Boolean) {
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(stringResource(R.string.debug_answer, number), style = MaterialTheme.typography.titleMedium)
            Text(
                entry.answer,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
            )
            HorizontalDivider()
            Text(stringResource(R.string.debug_model_calls), style = MaterialTheme.typography.labelLarge)
            entry.trace.usage.forEach { UsageRow(it) }
            HorizontalDivider()
            Text(stringResource(R.string.debug_tool_calls), style = MaterialTheme.typography.labelLarge)
            val calls = entry.trace.toolCalls.sortedBy { it.seq }
            if (calls.isEmpty()) {
                Text(stringResource(R.string.debug_no_tool_calls), style = MaterialTheme.typography.bodySmall)
            } else {
                Text(
                    stringResource(R.string.debug_tools_latency, calls.sumOf { it.latencyMs }),
                    style = MaterialTheme.typography.bodySmall,
                )
                calls.forEach { ToolCallCard(entry.messageId, it, expanded) }
            }
        }
    }
}

@Composable
private fun UsageRow(usage: UsageRecord) {
    val notReported = stringResource(R.string.debug_not_reported)
    Column {
        Text(usage.model, style = MaterialTheme.typography.bodyMedium, fontFamily = FontFamily.Monospace)
        Text(
            stringResource(
                R.string.debug_tokens,
                usage.inputTokens?.toString() ?: notReported,
                usage.outputTokens?.toString() ?: notReported,
            ),
            style = MaterialTheme.typography.bodySmall,
        )
        Text(
            stringResource(R.string.debug_cost, usage.costUsd?.content?.let { "$it USD" } ?: notReported),
            style = MaterialTheme.typography.bodySmall,
        )
    }
}

@Composable
private fun ToolCallCard(messageId: String, call: ToolCallRecord, expanded: Boolean) {
    OutlinedCard(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(8.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text(
                stringResource(R.string.debug_tool_header, call.seq, call.toolName, call.latencyMs),
                style = MaterialTheme.typography.bodyMedium,
                fontFamily = FontFamily.Monospace,
            )
            call.error?.let {
                Text(
                    stringResource(R.string.debug_tool_error, it),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.error,
                )
            }
            JsonSection("$messageId-${call.seq}-args", stringResource(R.string.debug_args), call.args, expanded)
            JsonSection("$messageId-${call.seq}-result", stringResource(R.string.debug_result), call.result, expanded)
        }
    }
}

/** A titled, foldable block of indented JSON (selectable, scrolls sideways for long lines). */
@Composable
private fun JsonSection(key: String, title: String, json: JsonObject?, initiallyExpanded: Boolean) {
    var open by rememberSaveable(key) { mutableStateOf(initiallyExpanded) }
    Row(
        Modifier.fillMaxWidth().clickable { open = !open }.padding(vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(
            if (open) Icons.Filled.KeyboardArrowUp else Icons.Filled.KeyboardArrowDown,
            contentDescription = stringResource(if (open) R.string.collapse else R.string.expand),
        )
        Text(title, style = MaterialTheme.typography.labelLarge)
    }
    if (open) {
        val text = json?.let { PrettyJson.encodeToString(JsonObject.serializer(), it) }
            ?: stringResource(R.string.debug_no_result)
        SelectionContainer {
            Text(
                text,
                style = MaterialTheme.typography.bodySmall,
                fontFamily = FontFamily.Monospace,
                softWrap = false,
                modifier = Modifier.horizontalScroll(rememberScrollState()),
            )
        }
    }
}

// --- Previews ----------------------------------------------------------------------------------

@Composable
private fun previewContent(): DebugUiState.Content {
    val trace: TraceOut = PreviewFixtures.load("trace")
    val answer = PreviewFixtures.load<List<MessageOut>>("messages").first { it.role == Role.ASSISTANT }
    return DebugUiState.Content(listOf(DebugEntry(trace.messageId, answer.content, trace)), focusIndex = 0)
}

@Preview(showBackground = true)
@Composable
private fun DebugContentPreview() {
    Series76AssistantTheme { DebugScreen(previewContent(), {}, {}) }
}

@Preview(showBackground = true)
@Composable
private fun DebugForbiddenPreview() {
    Series76AssistantTheme { DebugScreen(DebugUiState.Forbidden, {}, {}) }
}

@Preview(showBackground = true)
@Composable
private fun DebugErrorPreview() {
    Series76AssistantTheme { DebugScreen(DebugUiState.Error(AppError.Offline), {}, {}) }
}
