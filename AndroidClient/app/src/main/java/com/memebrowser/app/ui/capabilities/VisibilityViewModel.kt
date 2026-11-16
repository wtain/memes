package com.memebrowser.app.ui.capabilities

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.memebrowser.app.data.capabilities.CapabilitiesRepository
import com.memebrowser.app.data.capabilities.FeatureVisibility
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.stateIn
import javax.inject.Inject

/** Exposes what the selected backend lets the app show; NavGraph passes it down to the screens. */
@HiltViewModel
class VisibilityViewModel @Inject constructor(
    capabilities: CapabilitiesRepository
) : ViewModel() {
    val visibility: StateFlow<FeatureVisibility> = capabilities.visibility
        .stateIn(viewModelScope, SharingStarted.Eagerly, FeatureVisibility.ALL)
}
