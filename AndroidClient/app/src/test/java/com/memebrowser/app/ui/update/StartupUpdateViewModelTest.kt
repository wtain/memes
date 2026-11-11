package com.memebrowser.app.ui.update

import com.memebrowser.app.data.settings.AppSettingsStore
import com.memebrowser.app.data.update.UpdateManager
import com.memebrowser.app.data.update.UpdateStatus
import com.memebrowser.app.util.MainDispatcherRule
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import io.mockk.verify
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.flowOf
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Rule
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class StartupUpdateViewModelTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    private val status = MutableStateFlow<UpdateStatus>(UpdateStatus.Idle)
    private lateinit var updates: UpdateManager
    private lateinit var settings: AppSettingsStore

    @Before
    fun setup() {
        updates = mockk(relaxed = true)
        every { updates.status } returns status
        settings = mockk(relaxed = true)
        every { settings.checkUpdatesOnStartup } returns flowOf(true)
        every { settings.skippedUpdateTag } returns flowOf(null)
    }

    private fun checkFinishesWith(result: UpdateStatus) {
        every { updates.checkForUpdates() } answers { status.value = result }
    }

    @Test
    fun `newer version is offered`() {
        checkFinishesWith(UpdateStatus.Available("android-v2.0.0", "https://x/app.apk"))
        val vm = StartupUpdateViewModel(settings, updates)
        assertEquals(StartupUpdateDialog.Offer("android-v2.0.0", "https://x/app.apk"), vm.dialog.value)
    }

    @Test
    fun `switched off - no check and no dialog`() {
        every { settings.checkUpdatesOnStartup } returns flowOf(false)
        val vm = StartupUpdateViewModel(settings, updates)
        verify(exactly = 0) { updates.checkForUpdates() }
        assertEquals(StartupUpdateDialog.Hidden, vm.dialog.value)
    }

    @Test
    fun `up to date or failed check shows nothing`() {
        checkFinishesWith(UpdateStatus.UpToDate)
        assertEquals(StartupUpdateDialog.Hidden, StartupUpdateViewModel(settings, updates).dialog.value)

        status.value = UpdateStatus.Idle
        checkFinishesWith(UpdateStatus.Error("offline"))
        assertEquals(StartupUpdateDialog.Hidden, StartupUpdateViewModel(settings, updates).dialog.value)
    }

    @Test
    fun `a version the user declined is not offered again`() {
        every { settings.skippedUpdateTag } returns flowOf("android-v2.0.0")
        checkFinishesWith(UpdateStatus.Available("android-v2.0.0", "https://x/app.apk"))
        assertEquals(StartupUpdateDialog.Hidden, StartupUpdateViewModel(settings, updates).dialog.value)
    }

    @Test
    fun `No hides the dialog and remembers the version`() {
        checkFinishesWith(UpdateStatus.Available("android-v2.0.0", "https://x/app.apk"))
        val vm = StartupUpdateViewModel(settings, updates)
        vm.decline()
        assertEquals(StartupUpdateDialog.Hidden, vm.dialog.value)
        coVerify { settings.setSkippedUpdateTag("android-v2.0.0") }
        verify(exactly = 0) { updates.downloadUpdate(any()) }
    }

    @Test
    fun `Yes starts the download and shows progress`() {
        checkFinishesWith(UpdateStatus.Available("android-v2.0.0", "https://x/app.apk"))
        val vm = StartupUpdateViewModel(settings, updates)
        vm.install()
        verify { updates.downloadUpdate("https://x/app.apk") }
        status.value = UpdateStatus.Downloading(0.5f)
        assertEquals(StartupUpdateDialog.Downloading(0.5f), vm.dialog.value)
    }
}
