package com.memebrowser.app.ui.sync

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.memebrowser.app.data.album.GalleryAlbum
import com.memebrowser.app.data.album.MediaAccess
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
        var gallery = 0
        show(SyncUiState(loaded = true, environment = EnvironmentInfo("env-a", "General")), SyncActions(onChooseAlbum = { chosen++ }, onChooseGallery = { gallery++ }))
        composeTestRule.onNodeWithText("Environment: General").assertIsDisplayed()
        composeTestRule.onNodeWithText("Gallery album...").performClick()
        composeTestRule.onNodeWithText("Folder...").performClick()
        assertEquals(1, gallery)
        assertEquals(1, chosen)
    }

    @Test
    fun topBar_hasTheMenuButton_notABackArrow() {
        var menu = 0
        show(loaded, SyncActions(onMenu = { menu++ }))
        composeTestRule.onNodeWithContentDescription("Open menu").performClick()
        assertEquals(1, menu)
        composeTestRule.onNodeWithContentDescription("Back").assertDoesNotExist()
    }

    private val camera = GalleryAlbum("1", "Camera", 1234, "DCIM/Camera", 0L)
    private val shots = GalleryAlbum("2", "Screenshots", 87, null, 0L)

    @Test
    fun chooser_listsAlbumsAndReportsTheChoice() {
        var picked: GalleryAlbum? = null
        show(loaded.copy(chooser = ChooserState.Albums(listOf(camera, shots))), SyncActions(onChooseGalleryAlbum = { picked = it }))
        composeTestRule.onNodeWithText("Camera").assertIsDisplayed()
        composeTestRule.onNodeWithText("1234 images").assertIsDisplayed()
        composeTestRule.onNodeWithText("DCIM/Camera").assertIsDisplayed()
        composeTestRule.onNodeWithText("Screenshots").performClick()
        assertEquals(shots, picked)
    }

    @Test
    fun chooser_emptyLoadingAndFailedHaveText() {
        show(loaded.copy(chooser = ChooserState.Empty))
        composeTestRule.onNodeWithText("No albums with images were found on this phone.").assertIsDisplayed()
    }

    @Test
    fun partialAccess_showsTextAndOpensSettings() {
        var settings = 0
        show(loaded.copy(chooser = ChooserState.NeedsPermission(MediaAccess.PARTIAL)), SyncActions(onOpenAppSettings = { settings++ }))
        composeTestRule.onNodeWithText(permissionCardText(MediaAccess.PARTIAL)).assertIsDisplayed()
        composeTestRule.onNodeWithText("Open settings").performClick()
        assertEquals(1, settings)
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
    fun running_showsStopAndLeaveTheAppHint() {
        var stopped = 0
        show(
            loaded.copy(sync = SyncState.Running(1, 2, 50, SyncSummary(uploaded = 50))),
            SyncActions(onStop = { stopped++ }),
        )
        composeTestRule.onNodeWithText("You can leave the app; progress shows in the notification").assertIsDisplayed()
        composeTestRule.onNodeWithText("Stop", substring = true).performClick()
        assertEquals(1, stopped)
    }

    @Test
    fun retrySwitches_renderAndReportChanges() {
        var retry: Boolean? = null
        var wifi: Boolean? = null
        show(loaded, SyncActions(onAutoRetryChange = { retry = it }, onWifiOnlyChange = { wifi = it }))
        composeTestRule.onNodeWithText("Retry automatically").assertIsDisplayed()
        composeTestRule.onNodeWithText("Wi-Fi only").assertIsDisplayed()
        composeTestRule.onNodeWithContentDescription("Retry automatically").performClick()
        composeTestRule.onNodeWithContentDescription("Wi-Fi only").performClick()
        assertEquals(false, retry)
        assertEquals(false, wifi)
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
    fun permissionLost_galleryAlbumReopensTheGalleryChooser() {
        var gallery = 0
        var folder = 0
        show(
            loaded.copy(album = AlbumSelection("mediastore-bucket:42/Camera", "Camera"), scan = ScanState.PermissionLost),
            SyncActions(onChooseGallery = { gallery++ }, onChooseAlbum = { folder++ }),
        )
        composeTestRule.onNodeWithText(ACCESS_LOST_HELP_GALLERY).assertIsDisplayed()
        composeTestRule.onNodeWithText("Choose album again").performClick()
        assertEquals(1, gallery)
        assertEquals(0, folder)
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

    private val galleryState = loaded.copy(
        album = AlbumSelection(com.memebrowser.app.data.album.AlbumKey.mediaStore(7, "3 - memes"), "3 - memes"),
        removalSupported = true,
    )

    @Test
    fun removeSynced_confirmationShowsTheCountAndReportsTheChoice() {
        var confirmed = 0
        var cancelled = 0
        show(
            galleryState.copy(removal = RemovalState.Confirm(count = 3, kept = 1)),
            SyncActions(onConfirmRemoval = { confirmed++ }, onCancelRemoval = { cancelled++ }),
        )
        composeTestRule.onNodeWithText("Move 3 photos to the trash?").assertIsDisplayed()
        composeTestRule.onNodeWithText("Move 3").performClick()
        composeTestRule.onNodeWithText("Cancel").performClick()
        assertEquals(1, confirmed)
        assertEquals(1, cancelled)
    }

    @Test
    fun removeSynced_buttonIsOnlyForGalleryAlbums() {
        var asked = 0
        show(galleryState, SyncActions(onRemoveSynced = { asked++ }))
        composeTestRule.onNodeWithText("Move synced photos to the trash...").performClick()
        assertEquals(1, asked)
    }
}
