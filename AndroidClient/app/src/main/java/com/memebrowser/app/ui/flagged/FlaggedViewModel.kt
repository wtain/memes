package com.memebrowser.app.ui.flagged

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.memebrowser.app.data.model.Meme
import com.memebrowser.app.data.repository.MemeRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

data class FlaggedUiState(
    val items: List<Meme> = emptyList(),
    val isLoading: Boolean = false,
    val isLoadingMore: Boolean = false,
    val error: String? = null,
    val nextCursor: String? = null,
    val hasNext: Boolean = false
)

@HiltViewModel
class FlaggedViewModel @Inject constructor(
    private val repo: MemeRepository
) : ViewModel() {

    private val _state = MutableStateFlow(FlaggedUiState())
    val state: StateFlow<FlaggedUiState> = _state.asStateFlow()

    init { load() }

    fun load() {
        viewModelScope.launch {
            _state.update { it.copy(isLoading = true, error = null, items = emptyList(), nextCursor = null, hasNext = false) }
            repo.getFlagged(cursor = null)
                .onSuccess { response ->
                    _state.update { it.copy(
                        items = response.items ?: emptyList(),
                        nextCursor = response.nextCursor,
                        hasNext = response.hasNext ?: false,
                        isLoading = false
                    ) }
                }
                .onFailure { e ->
                    _state.update { it.copy(error = e.message, isLoading = false) }
                }
        }
    }

    fun loadMore() {
        val s = _state.value
        if (!s.hasNext || s.isLoadingMore || s.nextCursor == null) return
        viewModelScope.launch {
            _state.update { it.copy(isLoadingMore = true, error = null) }
            repo.getFlagged(cursor = s.nextCursor)
                .onSuccess { response ->
                    _state.update { it.copy(
                        items = (it.items + (response.items ?: emptyList())).distinctBy { m -> m.id },
                        nextCursor = response.nextCursor,
                        hasNext = response.hasNext ?: false,
                        isLoadingMore = false
                    ) }
                }
                .onFailure { e ->
                    _state.update { it.copy(error = e.message, isLoadingMore = false) }
                }
        }
    }

    fun dismissError() = _state.update { it.copy(error = null) }
}
