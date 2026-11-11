package com.memebrowser.app.ui.update

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.memebrowser.app.data.settings.AppSettingsStore
import com.memebrowser.app.data.update.UpdateManager
import com.memebrowser.app.data.update.UpdateStatus
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.launch
import javax.inject.Inject

/** What the startup dialog shows; [Hidden] means no dialog. */
sealed class StartupUpdateDialog {
    object Hidden : StartupUpdateDialog()
    data class Offer(val tag: String, val apkUrl: String) : StartupUpdateDialog()
    data class Downloading(val progress: Float) : StartupUpdateDialog()
    data class Failed(val message: String) : StartupUpdateDialog()
}

/**
 * Checks for an update once when it is created (the activity-scoped instance, so once per app launch, not per rotation),
 * if "Check for updates on startup" is on, and offers it in a dialog. A failed check shows nothing. Declining a version
 * means it is not offered again at startup; the About screen still offers it.
 */
@HiltViewModel
class StartupUpdateViewModel @Inject constructor(
    private val settings: AppSettingsStore,
    private val updates: UpdateManager,
) : ViewModel() {

    /** Set once the startup check found something worth offering, cleared when the user answers. */
    private val offered = MutableStateFlow(false)

    val dialog: StateFlow<StartupUpdateDialog> = combine(offered, updates.status) { isOffered, status ->
        if (!isOffered) return@combine StartupUpdateDialog.Hidden
        when (status) {
            is UpdateStatus.Available -> StartupUpdateDialog.Offer(status.tag, status.apkUrl)
            is UpdateStatus.Downloading -> StartupUpdateDialog.Downloading(status.progress)
            is UpdateStatus.Error -> StartupUpdateDialog.Failed(status.message)
            else -> StartupUpdateDialog.Hidden
        }
    }.stateIn(viewModelScope, SharingStarted.Eagerly, StartupUpdateDialog.Hidden)

    init {
        viewModelScope.launch {
            if (!settings.checkUpdatesOnStartup.first()) return@launch
            updates.checkForUpdates()
            // Idle is the state before the check starts; Checking is in flight.
            val result = updates.status.first { it !is UpdateStatus.Idle && it !is UpdateStatus.Checking }
            if (result is UpdateStatus.Available && result.tag != settings.skippedUpdateTag.first()) {
                offered.value = true
            }
        }
    }

    fun install() {
        val offer = dialog.value as? StartupUpdateDialog.Offer ?: return
        updates.downloadUpdate(offer.apkUrl)
    }

    /** "No": remember the version so it is not asked again at startup. */
    fun decline() {
        val offer = dialog.value as? StartupUpdateDialog.Offer ?: return
        offered.value = false
        viewModelScope.launch { settings.setSkippedUpdateTag(offer.tag) }
    }

    /** Closes the failure message, or hides the progress (the download goes on in the background). */
    fun dismiss() {
        val wasFailed = dialog.value is StartupUpdateDialog.Failed
        offered.value = false
        if (wasFailed) updates.dismissError()
    }
}
