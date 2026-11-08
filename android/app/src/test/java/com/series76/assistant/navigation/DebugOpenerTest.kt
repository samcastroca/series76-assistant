package com.series76.assistant.navigation

import com.series76.assistant.data.api.model.UserOut
import com.series76.assistant.data.api.model.UserRole
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** The navigation decides who gets a way into Debug: an engineer gets none at all. */
class DebugOpenerTest {
    private val opened = mutableListOf<DebugDestination>()

    @Test
    fun `an engineer gets no Debug entry`() {
        assertNull(debugOpener(UserOut("e@example.com", UserRole.ENGINEER)) { opened += it })
    }

    @Test
    fun `a developer opens Debug for a conversation or one of its answers`() {
        val open = checkNotNull(debugOpener(UserOut("d@example.com", UserRole.DEVELOPER)) { opened += it })
        open("s1", "m1")
        open("s1", null)
        assertEquals(listOf(DebugDestination("s1", "m1"), DebugDestination("s1", null)), opened)
    }
}
