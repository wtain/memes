package com.memebrowser.app.ui.search

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.memebrowser.app.data.model.Facet
import com.memebrowser.app.data.model.Meme
import com.memebrowser.app.data.repository.EnvironmentRepository
import com.memebrowser.app.data.repository.MemeRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.FlowPreview
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

enum class HealthStatus { Unknown, Online, Offline }

data class ActiveFacet(val category: String, val value: String) {
    override fun toString() = "$category:$value"
}

data class SearchUiState(
    val query: String = "",
    val items: List<Meme> = emptyList(),
    val facets: List<Facet> = emptyList(),
    val activeFacets: List<ActiveFacet> = emptyList(),
    val isLoading: Boolean = false,
    val isLoadingMore: Boolean = false,
    val error: String? = null,
    val nextCursor: String? = null,
    val hasNext: Boolean = false,
    val healthStatus: HealthStatus = HealthStatus.Unknown
)

@HiltViewModel
class SearchViewModel @Inject constructor(
    private val repo: MemeRepository,
    private val envRepo: EnvironmentRepository
) : ViewModel() {

    private val _state = MutableStateFlow(SearchUiState())
    val state: StateFlow<SearchUiState> = _state.asStateFlow()

    private var searchJob: Job? = null

    init {
        triggerSearch()
        checkHealth()
    }

    fun onQueryChange(query: String) {
        _state.update { it.copy(query = query) }
        searchJob?.cancel()
        searchJob = viewModelScope.launch {
            delay(400)
            triggerSearch()
        }
    }

    fun onFacetToggle(category: String, value: String) {
        val facet = ActiveFacet(category, value)
        val current = _state.value.activeFacets
        val updated = if (current.contains(facet)) current - facet else current + facet
        _state.update { it.copy(activeFacets = updated) }
        triggerSearch()
    }

    fun removeFacet(facet: ActiveFacet) {
        _state.update { it.copy(activeFacets = it.activeFacets - facet) }
        triggerSearch()
    }

    fun loadMore() {
        val s = _state.value
        if (!s.hasNext || s.isLoadingMore || s.nextCursor == null) return
        viewModelScope.launch {
            _state.update { it.copy(isLoadingMore = true, error = null) }
            repo.search(
                query = s.query.takeIf { it.isNotBlank() },
                facets = s.activeFacets.joinToString(",").takeIf { it.isNotEmpty() },
                cursor = s.nextCursor
            ).onSuccess { response ->
                _state.update { it.copy(
                    items = it.items + (response.items ?: emptyList()),
                    nextCursor = response.nextCursor,
                    hasNext = response.hasNext ?: false,
                    isLoadingMore = false
                )}
            }.onFailure { e ->
                _state.update { it.copy(error = e.message, isLoadingMore = false) }
            }
        }
    }

    fun checkHealth() {
        viewModelScope.launch {
            _state.update { it.copy(healthStatus = HealthStatus.Unknown) }
            repo.health()
                .onSuccess { _state.update { it.copy(healthStatus = HealthStatus.Online) } }
                .onFailure { _state.update { it.copy(healthStatus = HealthStatus.Offline) } }
        }
    }

    fun dismissError() = _state.update { it.copy(error = null) }

    private fun triggerSearch() {
        val s = _state.value
        viewModelScope.launch {
            _state.update { it.copy(isLoading = true, error = null, items = emptyList(), nextCursor = null, hasNext = false) }
            repo.search(
                query = s.query.takeIf { it.isNotBlank() },
                facets = s.activeFacets.joinToString(",").takeIf { it.isNotEmpty() },
                cursor = null
            ).onSuccess { response ->
                _state.update { it.copy(
                    items = response.items ?: emptyList(),
                    facets = response.facets ?: emptyList(),
                    nextCursor = response.nextCursor,
                    hasNext = response.hasNext ?: false,
                    isLoading = false
                )}
            }.onFailure { e ->
                _state.update { it.copy(error = e.message, isLoading = false) }
            }
        }
    }
}