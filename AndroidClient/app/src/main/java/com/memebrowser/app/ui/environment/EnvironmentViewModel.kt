package com.memebrowser.app.ui.environment

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.memebrowser.app.data.model.BackendEnvironment
import com.memebrowser.app.data.repository.EnvironmentRepository
import com.memebrowser.app.data.repository.EnvironmentWithSelection
import com.memebrowser.app.data.repository.MemeRepository
import com.memebrowser.app.data.repository.isValidCollectionName
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

enum class EnvHealthStatus { Unknown, Checking, Online, Offline }

data class EnvironmentUiState(
    val healthMap: Map<String, EnvHealthStatus> = emptyMap(),
    val error: String? = null
)

@HiltViewModel
class EnvironmentViewModel @Inject constructor(
    private val envRepo: EnvironmentRepository,
    private val memeRepo: MemeRepository
) : ViewModel() {

    val environments: StateFlow<List<EnvironmentWithSelection>> =
        envRepo.environmentsWithSelection.stateIn(
            viewModelScope,
            SharingStarted.WhileSubscribed(5000),
            emptyList()
        )

    private val _uiState = MutableStateFlow(EnvironmentUiState())
    val uiState: StateFlow<EnvironmentUiState> = _uiState.asStateFlow()

    fun selectEnvironment(env: BackendEnvironment) {
        viewModelScope.launch {
            envRepo.selectEnvironment(env)
            checkHealth(env.id, env.baseUrl)
        }
    }

    fun addEnvironment(name: String, baseUrl: String) {
        val trimmed = name.trim()
        if (!isValidCollectionName(trimmed)) {
            _uiState.update { it.copy(error = "Invalid collection name: only letters, digits, spaces, hyphens, and underscores are allowed, starting with a letter or digit") }
            return
        }
        viewModelScope.launch {
            runCatching { envRepo.addEnvironment(trimmed, baseUrl.trim()) }
                .onFailure { e -> _uiState.update { it.copy(error = e.message) } }
        }
    }

    fun updateEnvironment(id: String, name: String, baseUrl: String) {
        val trimmed = name.trim()
        if (!isValidCollectionName(trimmed)) {
            _uiState.update { it.copy(error = "Invalid collection name: only letters, digits, spaces, hyphens, and underscores are allowed, starting with a letter or digit") }
            return
        }
        viewModelScope.launch {
            runCatching { envRepo.updateEnvironment(id, trimmed, baseUrl.trim()) }
                .onFailure { e -> _uiState.update { it.copy(error = e.message) } }
        }
    }

    fun deleteEnvironment(id: String) {
        viewModelScope.launch {
            runCatching { envRepo.deleteEnvironment(id) }
                .onFailure { e -> _uiState.update { it.copy(error = e.message) } }
        }
    }

    fun checkHealth(envId: String, baseUrl: String) {
        _uiState.update { it.copy(healthMap = it.healthMap + (envId to EnvHealthStatus.Checking)) }
        viewModelScope.launch {
            memeRepo.healthCheck(baseUrl)
                .onSuccess {
                    _uiState.update { s -> s.copy(healthMap = s.healthMap + (envId to EnvHealthStatus.Online)) }
                }
                .onFailure {
                    _uiState.update { s -> s.copy(healthMap = s.healthMap + (envId to EnvHealthStatus.Offline)) }
                }
        }
    }

    fun checkAllHealth() {
        val envs = environments.value
        envs.forEach { checkHealth(it.environment.id, it.environment.baseUrl) }
    }

    fun dismissError() = _uiState.update { it.copy(error = null) }
}