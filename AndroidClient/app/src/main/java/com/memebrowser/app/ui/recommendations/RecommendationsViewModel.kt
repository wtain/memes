package com.memebrowser.app.ui.recommendations

import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.memebrowser.app.data.model.Meme
import com.memebrowser.app.data.repository.MemeRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

data class RecommendationsUiState(
    val query: String = "",
    val items: List<Meme> = emptyList(),
    val isLoading: Boolean = false,
    val isLoadingMore: Boolean = false,
    val error: String? = null,
    val nextCursor: String? = null,
    val hasNext: Boolean = false
)

@HiltViewModel
class RecommendationsViewModel @Inject constructor(
    savedStateHandle: SavedStateHandle,
    private val repo: MemeRepository
) : ViewModel() {

    private val _state = MutableStateFlow(
        RecommendationsUiState(query = savedStateHandle.get<String>("query") ?: "")
    )
    val state: StateFlow<RecommendationsUiState> = _state.asStateFlow()

    private var searchJob: Job? = null

    init { load() }

    fun onQueryChange(query: String) {
        _state.update { it.copy(query = query) }
        searchJob?.cancel()
        searchJob = viewModelScope.launch {
            delay(400)
            load()
        }
    }

    fun loadMore() {
        val s = _state.value
        if (!s.hasNext || s.isLoadingMore || s.nextCursor == null) return
        viewModelScope.launch {
            _state.update { it.copy(isLoadingMore = true) }
            repo.getRecommendations(
                query = s.query.takeIf { it.isNotBlank() },
                cursor = s.nextCursor
            ).onSuccess { response ->
                _state.update {
                    it.copy(
                        items = it.items + (response.items ?: emptyList()),
                        nextCursor = response.nextCursor,
                        hasNext = response.hasNext ?: false,
                        isLoadingMore = false
                    )
                }
            }.onFailure { e ->
                _state.update { it.copy(error = e.message, isLoadingMore = false) }
            }
        }
    }

    fun dismissError() = _state.update { it.copy(error = null) }

    private fun load() {
        val query = _state.value.query
        viewModelScope.launch {
            _state.update { it.copy(isLoading = true, error = null, items = emptyList(), nextCursor = null, hasNext = false) }
            repo.getRecommendations(
                query = query.takeIf { it.isNotBlank() },
                cursor = null
            ).onSuccess { response ->
                _state.update {
                    it.copy(
                        items = response.items ?: emptyList(),
                        nextCursor = response.nextCursor,
                        hasNext = response.hasNext ?: false,
                        isLoading = false
                    )
                }
            }.onFailure { e ->
                _state.update { it.copy(error = e.message, isLoading = false) }
            }
        }
    }
}