package com.series76.assistant.ui.chat.components

import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LocalContentColor
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.SuggestionChip
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.platform.UriHandler
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import com.mikepenz.markdown.m3.Markdown
import com.mikepenz.markdown.m3.markdownColor
import com.mikepenz.markdown.m3.markdownTypography
import com.series76.assistant.R
import com.series76.assistant.data.AppError
import com.series76.assistant.data.api.model.ConversationResponse
import com.series76.assistant.data.api.model.TurnWarning
import com.series76.assistant.ui.chat.ChatItem
import com.series76.assistant.ui.chat.toChatItem
import com.series76.assistant.ui.common.errorText
import com.series76.assistant.ui.preview.PreviewFixtures
import com.series76.assistant.ui.theme.Series76AssistantTheme

private val BubbleMaxWidth = 320.dp

private object InertUriHandler : UriHandler {
    override fun openUri(uri: String) = Unit
}

@Composable
fun UserBubble(
    item: ChatItem.User,
    canRetry: Boolean,
    onRetry: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Column(modifier.fillMaxWidth(), horizontalAlignment = Alignment.End) {
        Surface(
            color = if (item.failed) MaterialTheme.colorScheme.errorContainer else MaterialTheme.colorScheme.primary,
            contentColor = if (item.failed) MaterialTheme.colorScheme.onErrorContainer else MaterialTheme.colorScheme.onPrimary,
            shape = RoundedCornerShape(16.dp, 16.dp, 4.dp, 16.dp),
            modifier = Modifier.widthIn(max = BubbleMaxWidth),
        ) {
            Text(item.text, Modifier.padding(horizontal = 12.dp, vertical = 8.dp))
        }
        if (item.failed) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                Icon(Icons.Filled.Warning, contentDescription = null, tint = MaterialTheme.colorScheme.error)
                Text(
                    item.error?.let { errorText(it) } ?: stringResource(R.string.error_send_failed),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.error,
                    modifier = Modifier.widthIn(max = 240.dp),
                )
            }
            if (canRetry && item.error?.retryable != false) {
                TextButton(onClick = onRetry) {
                    Icon(Icons.Filled.Refresh, contentDescription = null)
                    Text(stringResource(R.string.retry), Modifier.padding(start = 4.dp))
                }
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun AssistantBubble(
    item: ChatItem.Assistant,
    chipsEnabled: Boolean,
    onChip: (String) -> Unit,
    modifier: Modifier = Modifier,
    /** Opens this answer's trace. Null (engineers): the icon is not composed at all. */
    onDebug: (() -> Unit)? = null,
) {
    Column(modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Row(verticalAlignment = Alignment.Bottom) {
            Surface(
                color = MaterialTheme.colorScheme.surfaceVariant,
                shape = RoundedCornerShape(16.dp, 16.dp, 16.dp, 4.dp),
                modifier = Modifier.widthIn(max = BubbleMaxWidth).weight(1f, fill = false),
            ) {
                // The model writes Markdown (bold, lists); render it instead of showing the markers.
                // Links it writes stay inert: a catalog answer has no reason to open external pages.
                CompositionLocalProvider(LocalUriHandler provides InertUriHandler) {
                    Markdown(
                        content = item.answer,
                        colors = markdownColor(text = LocalContentColor.current),
                        typography = markdownTypography(paragraph = MaterialTheme.typography.bodyLarge),
                        modifier = Modifier.padding(horizontal = 12.dp, vertical = 8.dp),
                    )
                }
            }
            if (onDebug != null) {
                IconButton(onClick = onDebug) {
                    Icon(
                        painterResource(R.drawable.ic_bug),
                        contentDescription = stringResource(R.string.debug_open_trace),
                        tint = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }
        item.warnings.forEach { warning ->
            Text(
                warningText(warning),
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.tertiary,
            )
        }
        item.products.forEach { ProductCard(it) }
        val options = item.clarification?.options.orEmpty()
        if (options.isNotEmpty()) ChipRow(options, chipsEnabled, onChip)
        if (item.suggestions.isNotEmpty()) {
            Text(stringResource(R.string.suggestions_label), style = MaterialTheme.typography.labelMedium)
            ChipRow(item.suggestions, chipsEnabled, onChip)
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun ChipRow(options: List<String>, enabled: Boolean, onChip: (String) -> Unit) {
    FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        options.forEach { option ->
            SuggestionChip(onClick = { onChip(option) }, label = { Text(option) }, enabled = enabled)
        }
    }
}

/** "Typing…" with a pulsing dot while a turn is in flight (the API does not stream). */
@Composable
fun TypingBubble(modifier: Modifier = Modifier) {
    val alpha by rememberInfiniteTransition(label = "typing").animateFloat(
        initialValue = 0.3f,
        targetValue = 1f,
        animationSpec = infiniteRepeatable(tween(600), RepeatMode.Reverse),
        label = "typing-alpha",
    )
    Box(modifier.fillMaxWidth()) {
        Surface(color = MaterialTheme.colorScheme.surfaceVariant, shape = RoundedCornerShape(16.dp)) {
            Text(
                stringResource(R.string.typing),
                style = MaterialTheme.typography.bodyMedium,
                modifier = Modifier.padding(horizontal = 12.dp, vertical = 8.dp).alpha(alpha),
            )
        }
    }
}

@Composable
private fun warningText(warning: TurnWarning) = stringResource(
    when (warning) {
        TurnWarning.TOLERANCE_APPLIED -> R.string.warning_tolerance_applied
        TurnWarning.SERVICE_TYPE_UNSPECIFIED -> R.string.warning_service_type_unspecified
        TurnWarning.NOT_CSA_CERTIFIED -> R.string.warning_not_csa_certified
        TurnWarning.NO_PUBLISHED_DATA -> R.string.warning_no_published_data
        TurnWarning.MULTIPLE_FILTERS_BLOCKING -> R.string.warning_multiple_filters_blocking
        TurnWarning.NUMBERS_REMOVED -> R.string.warning_numbers_removed
        TurnWarning.TOOL_ERROR -> R.string.warning_tool_error
    },
)

@Preview(showBackground = true)
@Composable
private fun AssistantBubbleRecommendationPreview() {
    val item = PreviewFixtures.load<ConversationResponse>("conversation_recommendation").toChatItem()
    Series76AssistantTheme { AssistantBubble(item.copy(products = item.products.take(2)), true, {}) }
}

@Preview(showBackground = true)
@Composable
private fun AssistantBubbleClarificationPreview() {
    val item = PreviewFixtures.load<ConversationResponse>("conversation_clarification").toChatItem()
    Series76AssistantTheme { AssistantBubble(item, true, {}) }
}

@Preview(showBackground = true)
@Composable
private fun AssistantBubbleNotFoundPreview() {
    val item = PreviewFixtures.load<ConversationResponse>("conversation_not_found").toChatItem()
    Series76AssistantTheme { AssistantBubble(item, true, {}) }
}

@Preview(showBackground = true)
@Composable
private fun UserBubbleFailedPreview() {
    Series76AssistantTheme {
        UserBubble(ChatItem.User("1", "¿Y la velocidad?", failed = true, error = AppError.Timeout), true, {})
    }
}

@Preview(showBackground = true)
@Composable
private fun TypingBubblePreview() {
    Series76AssistantTheme { TypingBubble(Modifier.padding(8.dp)) }
}
