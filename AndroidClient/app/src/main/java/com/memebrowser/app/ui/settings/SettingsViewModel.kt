package com.memebrowser.app.ui.settings

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.memebrowser.app.data.settings.AppSettingsStore
import com.memebrowser.app.data.tracker.TrackerSettingsStore
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import javax.inject.Inject

data class SettingsUiState(
    val checkUpdatesOnStartup: Boolean = AppSettingsStore.DEFAULT_CHECK_UPDATES,
    val uploadBatchSize: Int = TrackerSettingsStore.DEFAULT_BATCH_SIZE,
    val resultsPerPage: Int = AppSettingsStore.DEFAULT_RESULTS_PER_PAGE,
    val gridMinSizeDp: Int = AppSettingsStore.DEFAULT_GRID_MIN_SIZE_DP,
)

@HiltViewModel
class SettingsViewModel @Inject constructor(
    private val app: AppSettingsStore,
    private val tracker: TrackerSettingsStore,
) : ViewModel() {

    val state: StateFlow<SettingsUiState> = combine(
        app.checkUpdatesOnStartup,
        tracker.batchSize,
        app.resultsPerPage,
        app.gridMinSizeDp,
    ) { check, batch, perPage, grid -> SettingsUiState(check, batch, perPage, grid) }
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), SettingsUiState())

    fun setCheckUpdatesOnStartup(enabled: Boolean) {
        viewModelScope.launch { app.setCheckUpdatesOnStartup(enabled) }
    }

    fun setUploadBatchSize(size: Int) {
        viewModelScope.launch { tracker.setBatchSize(size) }
    }

    fun setResultsPerPage(size: Int) {
        viewModelScope.launch { app.setResultsPerPage(size) }
    }

    fun setGridMinSizeDp(size: Int) {
        viewModelScope.launch { app.setGridMinSizeDp(size) }
    }
}
