package com.series76.assistant.data.auth

import androidx.datastore.dataStoreFile
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.series76.assistant.data.api.model.UserRole
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Test
import org.junit.runner.RunWith

/**
 * The real encrypted store on a device: Android Keystore + Tink + DataStore. Uses its own file,
 * keyset and master key, so it never touches the app's signed-in session.
 */
@RunWith(AndroidJUnit4::class)
class TinkSessionStoreTest {
    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private val session = StoredSession("a-token-that-must-not-appear-in-clear", "e@example.com", UserRole.ENGINEER)

    @Test
    fun savesReadsAndClearsTheSessionEncrypted() = runTest {
        val store = TinkSessionStore(
            context,
            fileName = FILE,
            keysetPrefs = "test_session_keyset_prefs",
            masterKeyUri = "android-keystore://series76_test_session_master_key",
        )
        store.clear()
        assertNull(store.read())

        store.save(session)
        assertEquals(session, store.read())
        val onDisk = context.dataStoreFile(FILE).readBytes().decodeToString()
        assertFalse("token stored in clear", onDisk.contains(session.token))
        assertFalse("email stored in clear", onDisk.contains(session.email))

        store.clear()
        assertNull(store.read())
    }

    private companion object {
        const val FILE = "test_session.json"
    }
}
