package com.memebrowser.app.data.store

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import com.memebrowser.app.data.model.BackendEnvironment
import com.memebrowser.app.data.model.DEFAULT_ENVIRONMENTS
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import javax.inject.Inject
import javax.inject.Singleton

private val Context.dataStore: DataStore<Preferences> by preferencesDataStore(name = "settings")

@Singleton
class EnvironmentStore @Inject constructor(
    @ApplicationContext private val context: Context
) {
    private val SELECTED_ID = stringPreferencesKey("selected_environment_id")
    private val ENVIRONMENTS_JSON = stringPreferencesKey("environments_json")

    val selectedEnvironmentId: Flow<String> = context.dataStore.data.map { prefs ->
        prefs[SELECTED_ID] ?: DEFAULT_ENVIRONMENTS.first().id
    }

    val environments: Flow<List<BackendEnvironment>> = context.dataStore.data.map { prefs ->
        val json = prefs[ENVIRONMENTS_JSON]
        if (json == null) {
            DEFAULT_ENVIRONMENTS
        } else {
            try {
                Json.decodeFromString<List<BackendEnvironment>>(json)
            } catch (_: Exception) {
                DEFAULT_ENVIRONMENTS
            }
        }
    }

    suspend fun setSelectedEnvironmentId(id: String) {
        context.dataStore.edit { it[SELECTED_ID] = id }
    }

    suspend fun saveEnvironments(envs: List<BackendEnvironment>) {
        context.dataStore.edit { it[ENVIRONMENTS_JSON] = Json.encodeToString(envs) }
    }
}