package com.memebrowser.app.data.repository

import com.memebrowser.app.data.api.UrlProvider
import com.memebrowser.app.data.model.BackendEnvironment
import com.memebrowser.app.data.store.EnvironmentStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.first
import java.util.UUID
import javax.inject.Inject
import javax.inject.Singleton

data class EnvironmentWithSelection(
    val environment: BackendEnvironment,
    val isSelected: Boolean
)

@Singleton
open class EnvironmentRepository @Inject constructor(
    private val store: EnvironmentStore,
    private val urlProvider: UrlProvider
) {
    val environmentsWithSelection: Flow<List<EnvironmentWithSelection>> =
        combine(store.environments, store.selectedEnvironmentId) { envs, selectedId ->
            envs.map { EnvironmentWithSelection(it, it.id == selectedId) }
        }

    val selectedEnvironmentId: Flow<String> = store.selectedEnvironmentId

    suspend fun selectEnvironment(env: BackendEnvironment) {
        store.setSelectedEnvironmentId(env.id)
        urlProvider.setBaseUrl(env.baseUrl)
    }

    suspend fun addEnvironment(name: String, baseUrl: String) {
        val current = store.environments.first()
        val new = BackendEnvironment(
            id = UUID.randomUUID().toString(),
            name = name,
            baseUrl = baseUrl,
            isBuiltIn = false
        )
        store.saveEnvironments(current + new)
    }

    suspend fun updateEnvironment(id: String, name: String, baseUrl: String) {
        val current = store.environments.first()
        store.saveEnvironments(current.map { env ->
            if (env.id == id) env.copy(name = name, baseUrl = baseUrl) else env
        })
    }

    suspend fun deleteEnvironment(id: String) {
        val current = store.environments.first()
        store.saveEnvironments(current.filter { it.id != id || it.isBuiltIn })
    }
}