package com.memebrowser.app.ui.about

import android.content.Context
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.memebrowser.app.data.repository.BugReportUploader
import com.memebrowser.app.data.update.UpdateManager
import com.memebrowser.app.data.update.UpdateStatus
import dagger.hilt.android.lifecycle.HiltViewModel
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import javax.inject.Inject

@HiltViewModel
class AboutViewModel @Inject constructor(
    @ApplicationContext private val appContext: Context,
    private val bugReportUploader: BugReportUploader,
    private val updates: UpdateManager,
) : ViewModel() {

    val currentVersion: String = updates.currentVersion

    val updateStatus: StateFlow<UpdateStatus> = updates.status

    private val _bugReportMessage = MutableStateFlow<String?>(null)
    val bugReportMessage: StateFlow<String?> = _bugReportMessage.asStateFlow()

    fun sendBugReport() {
        viewModelScope.launch {
            bugReportUploader.sendBugReport(appContext) { message ->
                _bugReportMessage.value = message
            }
        }
    }

    fun dismissBugReportMessage() {
        _bugReportMessage.value = null
    }

    fun checkForUpdates() = updates.checkForUpdates()

    fun downloadUpdate(apkUrl: String, @Suppress("UNUSED_PARAMETER") tag: String) = updates.downloadUpdate(apkUrl)

    fun dismissError() = updates.dismissError()
}
