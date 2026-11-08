package com.series76.assistant.data

import com.series76.assistant.data.api.model.ApiErrorBody
import com.series76.assistant.data.api.model.ConversationResponse
import com.series76.assistant.data.api.model.MessageOut
import com.series76.assistant.data.api.model.MessageStatus
import com.series76.assistant.data.api.model.ResponseType
import com.series76.assistant.data.api.model.Role
import com.series76.assistant.data.api.model.SessionSummary
import com.series76.assistant.data.api.model.TurnWarning
import com.series76.assistant.testutil.Fixtures
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The Kotlin models decode every generated fixture with unknown keys rejected, so a field the
 * backend adds, renames or retypes fails here (regenerate with `make android-fixtures`).
 */
class ContractDecodingTest {
    private fun conversation(type: String) =
        Fixtures.load<ConversationResponse>("conversation_$type", Fixtures.strictJson)

    @Test
    fun `every response type fixture decodes strictly`() {
        val expected = mapOf(
            "detail" to ResponseType.DETAIL,
            "recommendation" to ResponseType.RECOMMENDATION,
            "comparison" to ResponseType.COMPARISON,
            "no_match" to ResponseType.NO_MATCH,
            "not_found" to ResponseType.NOT_FOUND,
            "no_data" to ResponseType.NO_DATA,
            "clarification" to ResponseType.CLARIFICATION,
        )
        expected.forEach { (name, type) -> assertEquals(name, type, conversation(name).responseType) }
    }

    @Test
    fun `detail carries one product with its services and both frequencies`() {
        val product = conversation("detail").products.single()
        assertTrue(product.dataAvailable)
        assertTrue(product.service.isNotEmpty())
        assertEquals(setOf(50, 60), product.electrical.mapNotNull { it.frequencyHz }.toSet())
    }

    @Test
    fun `unpublished values stay null instead of becoming zero`() {
        val response = conversation("no_data")
        val product = response.products.single()
        assertFalse(product.dataAvailable)
        assertNull(product.motorPowerW)
        product.service.forEach { assertNull(it.torque.inLb); assertNull(it.torque.nm) }
        product.electrical.forEach { assertNull(it.speedS); assertNull(it.flaA) }
        assertEquals(listOf(TurnWarning.NO_PUBLISHED_DATA), response.warnings)
    }

    @Test
    fun `closest alternatives are marked as not meeting the requirements`() {
        val response = conversation("no_match")
        assertTrue(response.products.isNotEmpty())
        assertTrue(response.products.none { it.meetsRequirements })
        assertTrue(response.products.all { it.unmet.isNotEmpty() })
    }

    @Test
    fun `clarification and suggestions decode`() {
        assertNotNull(conversation("clarification").clarification?.options?.takeIf { it.isNotEmpty() })
        val notFound = conversation("not_found")
        assertTrue(notFound.products.isEmpty())
        assertTrue(notFound.suggestions.isNotEmpty())
    }

    @Test
    fun `sessions and message history decode strictly`() {
        val sessions = Fixtures.load<List<SessionSummary>>("sessions", Fixtures.strictJson)
        assertTrue(sessions.isNotEmpty())
        assertTrue(sessions.all { it.updatedAt >= it.createdAt })

        val messages = Fixtures.load<List<MessageOut>>("messages", Fixtures.strictJson)
        assertTrue(messages.filter { it.role == Role.USER }.all { it.responseType == null })
        assertTrue(messages.filter { it.role == Role.ASSISTANT }.all { it.responseType != null })
        assertTrue(messages.any { it.products.isNotEmpty() })
        assertEquals(MessageStatus.ERROR, messages.last().status)
    }

    @Test
    fun `error fixtures use the single error shape`() {
        mapOf(
            "error_empty_query" to "empty_query",
            "error_session_not_found" to "session_not_found",
            "error_llm_unavailable" to "llm_unavailable",
            "error_budget_exceeded" to "budget_exceeded",
        ).forEach { (name, code) ->
            assertEquals(code, Fixtures.load<ApiErrorBody>(name, Fixtures.strictJson).error.code)
        }
    }
}
