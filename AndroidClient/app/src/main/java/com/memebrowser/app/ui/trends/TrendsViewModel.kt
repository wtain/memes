package com.memebrowser.app.ui.trends

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.memebrowser.app.data.model.TrendEntry
import com.memebrowser.app.data.repository.MemeRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

sealed class TrendsLoadState {
    object Idle : TrendsLoadState()
    object Loading : TrendsLoadState()
    data class Done(val entries: List<TrendEntry>) : TrendsLoadState()
    data class Error(val message: String) : TrendsLoadState()
    object NoData : TrendsLoadState()
}

data class TrendsUiState(
    val dates: List<String> = emptyList(),
    val selectedDate: String? = null,
    val loadState: TrendsLoadState = TrendsLoadState.Idle
)

@HiltViewModel
class TrendsViewModel @Inject constructor(
    private val repo: MemeRepository
) : ViewModel() {

    private val _state = MutableStateFlow(TrendsUiState())
    val state: StateFlow<TrendsUiState> = _state.asStateFlow()

    init { loadDates() }

    private fun loadDates() {
        viewModelScope.launch {
            repo.getTrendsDates()
                .onSuccess { dates ->
                    _state.update { it.copy(dates = dates) }
                    if (dates.isNotEmpty()) selectDate(dates[0])
                }
                .onFailure { e ->
                    _state.update { it.copy(loadState = TrendsLoadState.Error(e.message ?: "Failed to load dates")) }
                }
        }
    }

    fun selectDate(date: String) {
        _state.update { it.copy(selectedDate = date, loadState = TrendsLoadState.Loading) }
        viewModelScope.launch {
            repo.getLatestTrendsRun(date)
                .onSuccess { run ->
                    repo.getTrendsEntries(run.runId)
                        .onSuccess { entries ->
                            _state.update {
                                it.copy(
                                    loadState = if (entries.isEmpty()) TrendsLoadState.NoData
                                               else TrendsLoadState.Done(entries)
                                )
                            }
                        }
                        .onFailure { e ->
                            _state.update { it.copy(loadState = TrendsLoadState.Error(e.message ?: "Failed to load entries")) }
                        }
                }
                .onFailure {
                    _state.update { it.copy(loadState = TrendsLoadState.NoData) }
                }
        }
    }
}