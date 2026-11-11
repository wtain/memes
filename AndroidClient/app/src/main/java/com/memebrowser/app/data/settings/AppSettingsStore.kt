package com.memebrowser.app.data.settings

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.intPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map
import javax.inject.Inject
import javax.inject.Singleton

private val Context.appSettingsDataStore: DataStore<Preferences> by preferencesDataStore(name = "app_settings")

/**
 * App-wide preferences edited on the settings screen. The album-sync batch size is deliberately NOT here: it stays in
 * [com.memebrowser.app.data.tracker.TrackerSettingsStore] (the sync engine reads it) and the settings screen edits it there.
 */
@Singleton
class AppSettingsStore(private val dataStore: DataStore<Preferences>) {

    @Inject
    constructor(@ApplicationContext context: Context) : this(context.appSettingsDataStore)

    private val checkUpdatesKey = booleanPreferencesKey("check_updates_on_startup")
    private val resultsPerPageKey = intPreferencesKey("results_per_page")
    private val gridMinSizeKey = intPreferencesKey("grid_min_size_dp")
    private val skippedUpdateKey = stringPreferencesKey("skipped_update_tag")

    val checkUpdatesOnStartup: Flow<Boolean> = dataStore.data.map { it[checkUpdatesKey] ?: DEFAULT_CHECK_UPDATES }

    suspend fun setCheckUpdatesOnStartup(enabled: Boolean) {
        dataStore.edit { it[checkUpdatesKey] = enabled }
    }

    val resultsPerPage: Flow<Int> = dataStore.data.map {
        clampResultsPerPage(it[resultsPerPageKey] ?: DEFAULT_RESULTS_PER_PAGE)
    }

    suspend fun setResultsPerPage(size: Int) {
        dataStore.edit { it[resultsPerPageKey] = clampResultsPerPage(size) }
    }

    val gridMinSizeDp: Flow<Int> = dataStore.data.map {
        clampGridMinSize(it[gridMinSizeKey] ?: DEFAULT_GRID_MIN_SIZE_DP)
    }

    suspend fun setGridMinSizeDp(size: Int) {
        dataStore.edit { it[gridMinSizeKey] = clampGridMinSize(size) }
    }

    /** The release tag the user declined in the startup dialog; that version is not offered again at startup. */
    val skippedUpdateTag: Flow<String?> = dataStore.data.map { it[skippedUpdateKey] }

    suspend fun setSkippedUpdateTag(tag: String) {
        dataStore.edit { it[skippedUpdateKey] = tag }
    }

    companion object {
        const val DEFAULT_CHECK_UPDATES = true

        // The backend accepts 1..100 on /api/images.
        const val MIN_RESULTS_PER_PAGE = 10
        const val MAX_RESULTS_PER_PAGE = 100
        const val DEFAULT_RESULTS_PER_PAGE = 20

        const val MIN_GRID_MIN_SIZE_DP = 100
        const val MAX_GRID_MIN_SIZE_DP = 300
        const val DEFAULT_GRID_MIN_SIZE_DP = 160

        fun clampResultsPerPage(v: Int) = v.coerceIn(MIN_RESULTS_PER_PAGE, MAX_RESULTS_PER_PAGE)
        fun clampGridMinSize(v: Int) = v.coerceIn(MIN_GRID_MIN_SIZE_DP, MAX_GRID_MIN_SIZE_DP)
    }
}
