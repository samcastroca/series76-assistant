package com.series76.assistant.ui.auth

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotSame
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test

/** Leaving a session state really ends the ViewModels created under it. */
class BranchViewModelStoresTest {
    class Probe : ViewModel() {
        var cleared = false

        override fun onCleared() {
            cleared = true
        }
    }

    private val stores = BranchViewModelStores()

    private fun probeIn(branch: String): Probe = ViewModelProvider.create(stores.owner(branch))[Probe::class]

    @Test
    fun `signing out clears the signed-in ViewModels`() {
        val signedIn = probeIn("signed-in:a@example.com")
        stores.keepOnly("signed-out")
        assertTrue(signedIn.cleared)
    }

    @Test
    fun `the current branch keeps its ViewModels across recompositions`() {
        val login = probeIn("signed-out")
        stores.keepOnly("signed-out") // the same state again (rotation, recomposition)
        assertFalse(login.cleared)
        assertSame(login, probeIn("signed-out"))
    }

    @Test
    fun `the login form starts fresh on each visit`() {
        val first = probeIn("signed-out")
        stores.keepOnly("signed-in:a@example.com")
        assertTrue(first.cleared)
        assertNotSame(first, probeIn("signed-out"))
    }
}
