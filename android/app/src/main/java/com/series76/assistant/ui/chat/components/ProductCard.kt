package com.series76.assistant.ui.chat.components

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import com.series76.assistant.R
import com.series76.assistant.data.api.model.ConversationResponse
import com.series76.assistant.data.api.model.CurrentType
import com.series76.assistant.data.api.model.Enclosure
import com.series76.assistant.data.api.model.PowerSupply
import com.series76.assistant.data.api.model.Product
import com.series76.assistant.data.api.model.ServiceType
import com.series76.assistant.ui.preview.PreviewFixtures
import com.series76.assistant.ui.theme.Series76AssistantTheme

/**
 * A compact product card: part number, enclosure and supply chips, torque per service and motor
 * power, plus badges. Every value is read from the API's `products`; null shows "not published".
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun ProductCard(product: Product, modifier: Modifier = Modifier) {
    val colors = if (product.meetsRequirements) {
        CardDefaults.cardColors()
    } else {
        CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant)
    }
    Card(modifier = modifier.fillMaxWidth(), colors = colors) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text(
                product.partNumber,
                style = MaterialTheme.typography.titleSmall,
                fontWeight = FontWeight.SemiBold,
            )
            FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                AssistChip(onClick = {}, label = { Text(enclosureLabel(product.enclosure)) })
                AssistChip(onClick = {}, label = { Text(supplyLabel(product.powerSupply)) })
            }
            Badges(product)
            if (product.dataAvailable) {
                product.service.forEach { service ->
                    SpecRow(
                        label = serviceLabel(service.type),
                        value = if (service.torque.inLb == null && service.torque.nm == null) {
                            stringResource(R.string.not_published)
                        } else {
                            stringResource(
                                R.string.torque_value,
                                service.torque.inLb?.toString() ?: "—",
                                service.torque.nm?.toString() ?: "—",
                            )
                        },
                    )
                }
                SpecRow(
                    label = stringResource(R.string.motor_power),
                    value = product.motorPowerW?.let { stringResource(R.string.watts_value, it) }
                        ?: stringResource(R.string.not_published),
                )
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun Badges(product: Product) {
    val badges = buildList {
        if (!product.meetsRequirements) add(stringResource(R.string.badge_not_meeting))
        if (!product.csaCertified) add(stringResource(R.string.badge_not_csa))
        if (!product.dataAvailable) add(stringResource(R.string.badge_no_data))
    }
    if (badges.isEmpty()) return
    FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        badges.forEach { Badge(it) }
    }
}

@Composable
private fun Badge(text: String) {
    Surface(
        color = MaterialTheme.colorScheme.errorContainer,
        contentColor = MaterialTheme.colorScheme.onErrorContainer,
        shape = MaterialTheme.shapes.small,
    ) {
        Text(
            text,
            style = MaterialTheme.typography.labelSmall,
            modifier = Modifier.padding(horizontal = 8.dp, vertical = 2.dp),
        )
    }
}

@Composable
private fun SpecRow(label: String, value: String) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
        Text(label, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(value, style = MaterialTheme.typography.bodySmall, color = Color.Unspecified)
    }
}

@Composable
private fun enclosureLabel(enclosure: Enclosure) = when (enclosure) {
    Enclosure.WEATHERPROOF -> stringResource(R.string.enclosure_weatherproof)
    Enclosure.EXPLOSIONPROOF -> stringResource(R.string.enclosure_explosionproof)
}

@Composable
private fun serviceLabel(type: ServiceType) = when (type) {
    ServiceType.ON_OFF -> stringResource(R.string.service_on_off)
    ServiceType.MODULATING -> stringResource(R.string.service_modulating)
    ServiceType.UNSPECIFIED -> stringResource(R.string.service_unspecified)
}

/** "220 V · 1 fase · AC"; the phase is left out when the table gives none (24 V). */
@Composable
fun supplyLabel(supply: PowerSupply): String {
    val current = when (supply.currentType) {
        CurrentType.AC -> "AC"
        CurrentType.DC -> "DC"
        CurrentType.AC_DC -> "AC/DC"
    }
    return listOfNotNull(
        stringResource(R.string.voltage_value, supply.voltageV),
        supply.phase?.let { stringResource(R.string.phase_value, it) },
        current,
    ).joinToString(" · ")
}

@Preview(showBackground = true)
@Composable
private fun ProductCardDetailPreview() {
    val response = PreviewFixtures.load<ConversationResponse>("conversation_detail")
    Series76AssistantTheme { ProductCard(response.products.first(), Modifier.padding(8.dp)) }
}

@Preview(showBackground = true)
@Composable
private fun ProductCardAlternativePreview() {
    val response = PreviewFixtures.load<ConversationResponse>("conversation_no_match")
    Series76AssistantTheme { ProductCard(response.products.first(), Modifier.padding(8.dp)) }
}

@Preview(showBackground = true)
@Composable
private fun ProductCardNoDataPreview() {
    val response = PreviewFixtures.load<ConversationResponse>("conversation_no_data")
    Series76AssistantTheme { ProductCard(response.products.first(), Modifier.padding(8.dp)) }
}
