package com.series76.assistant.data.auth

import android.content.Context
import androidx.datastore.core.CorruptionException
import androidx.datastore.core.DataStore
import androidx.datastore.core.DataStoreFactory
import androidx.datastore.core.Serializer
import androidx.datastore.core.handlers.ReplaceFileCorruptionHandler
import androidx.datastore.dataStoreFile
import androidx.datastore.tink.AeadSerializer
import com.google.crypto.tink.Aead
import com.google.crypto.tink.KeyTemplate
import com.google.crypto.tink.RegistryConfiguration
import com.google.crypto.tink.aead.AeadConfig
import com.google.crypto.tink.aead.PredefinedAeadParameters
import com.google.crypto.tink.integration.android.AndroidKeysetManager
import com.series76.assistant.data.api.model.UserRole
import java.io.InputStream
import java.io.OutputStream
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.SerializationException
import kotlinx.serialization.json.Json

/** What survives an app restart: the token and who it belongs to. Never the password. */
@Serializable
data class StoredSession(val token: String, val email: String, val role: UserRole)

interface SessionStore {
    suspend fun read(): StoredSession?

    suspend fun save(session: StoredSession)

    suspend fun clear()
}

/**
 * [SessionStore] on a typed DataStore whose file is encrypted with Tink (AES-256-GCM). The Tink
 * keyset lives in SharedPreferences, itself encrypted by a key in the Android Keystore that never
 * leaves the device (ADR 0007). A file that cannot be decrypted (for example after a reinstall)
 * is replaced by an empty session: the user simply signs in again.
 *
 * If the Keystore is unavailable, Tink would keep its keyset in clear; then nothing is written to
 * disk and the session lasts only while the process lives.
 *
 * The names are parameters so the instrumented test does not touch the app's own session.
 */
class TinkSessionStore(
    private val context: Context,
    private val fileName: String = FILE_NAME,
    private val keysetPrefs: String = KEYSET_PREFS,
    private val masterKeyUri: String = MASTER_KEY_URI,
) : SessionStore {

    // Built on first use, off the main thread: creating the keyset touches the Keystore and disk.
    // Null when the Keystore cannot protect the keyset.
    private val dataStore: DataStore<SessionFile>? by lazy {
        aead()?.let { aead ->
            DataStoreFactory.create(
                serializer = AeadSerializer(
                    aead = aead,
                    wrappedSerializer = SessionFileSerializer,
                    associatedData = fileName.encodeToByteArray(), // binds the ciphertext to this file
                ),
                corruptionHandler = ReplaceFileCorruptionHandler { SessionFile() },
                produceFile = { context.dataStoreFile(fileName) },
            )
        }
    }

    override suspend fun read(): StoredSession? = store()?.data?.first()?.session

    override suspend fun save(session: StoredSession) {
        store()?.updateData { SessionFile(session) }
    }

    override suspend fun clear() {
        store()?.updateData { SessionFile() }
    }

    private suspend fun store(): DataStore<SessionFile>? = withContext(Dispatchers.IO) { dataStore }

    private fun aead(): Aead? {
        AeadConfig.register()
        val manager = AndroidKeysetManager.Builder()
            .withSharedPref(context, KEYSET_NAME, keysetPrefs)
            .withKeyTemplate(KeyTemplate.createFrom(PredefinedAeadParameters.AES256_GCM))
            .withMasterKeyUri(masterKeyUri)
            .build()
        if (!manager.isUsingKeystore) return null
        return manager.keysetHandle.getPrimitive(RegistryConfiguration.get(), Aead::class.java)
    }

    companion object {
        // Also excluded from backups in res/xml/backup_rules.xml and data_extraction_rules.xml.
        const val FILE_NAME = "session.json"
        const val KEYSET_PREFS = "session_keyset_prefs"
        private const val KEYSET_NAME = "session_keyset"
        private const val MASTER_KEY_URI = "android-keystore://series76_session_master_key"
    }
}

@Serializable
internal data class SessionFile(val session: StoredSession? = null)

internal object SessionFileSerializer : Serializer<SessionFile> {
    private val json = Json { ignoreUnknownKeys = true }

    override val defaultValue = SessionFile()

    override suspend fun readFrom(input: InputStream): SessionFile =
        try {
            json.decodeFromString(input.readBytes().decodeToString())
        } catch (e: SerializationException) {
            throw CorruptionException("Unreadable session file", e)
        }

    override suspend fun writeTo(t: SessionFile, output: OutputStream) {
        output.write(json.encodeToString(SessionFile.serializer(), t).encodeToByteArray())
    }
}
