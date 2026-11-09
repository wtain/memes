package com.memebrowser.app.data.tracker

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.intPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map
import javax.inject.Inject
import javax.inject.Singleton

private val Context.trackerDataStore: DataStore<Preferences> by preferencesDataStore(name = "tracker_settings")

data class AlbumSelection(val treeUri: String, val displayName: String)

/** Per-environment album choice and the global batch size. */
@Singleton
class TrackerSettingsStore(private val dataStore: DataStore<Preferences>) {

    @Inject
    constructor(@ApplicationContext context: Context) : this(context.trackerDataStore)

    private val batchSizeKey = intPreferencesKey("batch_size")
    private fun albumUriKey(environmentId: String) = stringPreferencesKey("album_uri_$environmentId")
    private fun albumNameKey(environmentId: String) = stringPreferencesKey("album_name_$environmentId")

    /** Null until the user selected an album for [environmentId]. */
    fun album(environmentId: String): Flow<AlbumSelection?> = dataStore.data.map { prefs ->
        val uri = prefs[albumUriKey(environmentId)] ?: return@map null
        AlbumSelection(uri, prefs[albumNameKey(environmentId)] ?: "")
    }

    suspend fun setAlbum(environmentId: String, treeUri: String, displayName: String) {
        dataStore.edit {
            it[albumUriKey(environmentId)] = treeUri
            it[albumNameKey(environmentId)] = displayName
        }
    }

    suspend fun clearAlbum(environmentId: String) {
        dataStore.edit {
            it.remove(albumUriKey(environmentId))
            it.remove(albumNameKey(environmentId))
        }
    }

    val batchSize: Flow<Int> = dataStore.data.map { clampBatchSize(it[batchSizeKey] ?: DEFAULT_BATCH_SIZE) }

    suspend fun setBatchSize(size: Int) {
        dataStore.edit { it[batchSizeKey] = clampBatchSize(size) }
    }

    companion object {
        const val MIN_BATCH_SIZE = 1
        const val MAX_BATCH_SIZE = 50
        const val DEFAULT_BATCH_SIZE = 50

        fun clampBatchSize(size: Int): Int = size.coerceIn(MIN_BATCH_SIZE, MAX_BATCH_SIZE)
    }
}
