package com.memebrowser.app.ui.sync

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.memebrowser.app.data.album.ScanState
import com.memebrowser.app.data.sync.SyncError
import com.memebrowser.app.data.sync.SyncState
import com.memebrowser.app.data.sync.SyncSummary
import com.memebrowser.app.data.tracker.AlbumSelection
import com.memebrowser.app.data.tracker.TrackerCounts
import com.memebrowser.app.ui.theme.MemeBrowserTheme
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/** Renders the stateless [SyncContent] for the main states. Needs a device or emulator. */
@RunWith(AndroidJUnit4::class)
class SyncScreenTest {

    @get:Rule
    val composeTestRule = createComposeRule()

    private val album = AlbumSelection("content://x/tree/primary%3ADCIM%2FCamera", "Camera")
    private val loaded = SyncUiState(
        loaded = true,
        environment = EnvironmentInfo("env-a", "General"),
        album = album,
        counts = TrackerCounts(uploaded = 4, supported = 10, rejected = 1, pending = 5),
    )

    private fun show(state: SyncUiState, actions: SyncActions = SyncActions()) {
        composeTestRule.setContent { MemeBrowserTheme { SyncContent(state, actions) } }
    }

    @Test
    fun noAlbum_showsChooseButton() {
        var chosen = 0
        show(SyncUiState(loaded = true, environment = EnvironmentInfo("env-a", "General")), SyncActions(onChooseAlbum = { chosen++ }))
        composeTestRule.onNodeWithText("Environment: General").assertIsDisplayed()
        composeTestRule.onNodeWithText("Choose album folder").performClick()
        assertEquals(1, chosen)
    }

    @Test
    fun idle_showsStatusAndSyncNow() {
        var synced = 0
        show(loaded, SyncActions(onSyncNow = { synced++ }))
        composeTestRule.onNodeWithText("4 of 10 uploaded").assertIsDisplayed()
        composeTestRule.onNodeWithText("5 pending").assertIsDisplayed()
        composeTestRule.onNodeWithText("1 rejected").assertIsDisplayed()
        composeTestRule.onNodeWithText("Sync now", substring = true).assertIsEnabled().performClick()
        assertEquals(1, synced)
    }

    @Test
    fun nothingPending_disablesSyncNow() {
        show(loaded.copy(counts = loaded.counts.copy(pending = 0)))
        composeTestRule.onNodeWithText("Sync now", substring = true).assertIsNotEnabled()
    }

    @Test
    fun running_showsStopAndKeepOpenHint() {
        var stopped = 0
        show(
            loaded.copy(sync = SyncState.Running(1, 2, 50, SyncSummary(uploaded = 50))),
            SyncActions(onStop = { stopped++ }),
        )
        composeTestRule.onNodeWithText("Keep the app open while syncing").assertIsDisplayed()
        composeTestRule.onNodeWithText("Stop", substring = true).performClick()
        assertEquals(1, stopped)
    }

    @Test
    fun failed_showsTheActionableMessage() {
        show(loaded.copy(sync = SyncState.Failed(SyncError.INBOX_FULL_OR_UNWRITABLE, "", SyncSummary(), 1)))
        composeTestRule.onNodeWithText(syncErrorMessage(SyncError.INBOX_FULL_OR_UNWRITABLE)).assertIsDisplayed()
    }

    @Test
    fun permissionLost_offersChooseAgain() {
        show(loaded.copy(scan = ScanState.PermissionLost))
        composeTestRule.onNodeWithText("Choose album again").assertIsDisplayed()
    }

    @Test
    fun changeDialog_showsCountsAndConfirms() {
        var confirmed = 0
        val plan = com.memebrowser.app.data.album.ChangeAlbumPlan(
            "env-a", "content://new", "Memes", album, uploaded = 5, pending = 3, rejected = 1,
            looksLikeAppOwnFolder = false, isSameAlbum = false, needsRegrant = false,
        )
        show(loaded.copy(changePlan = plan), SyncActions(onConfirmChange = { confirmed++ }))
        composeTestRule.onNodeWithText("Change and reset").performClick()
        assertEquals(1, confirmed)
    }
}
