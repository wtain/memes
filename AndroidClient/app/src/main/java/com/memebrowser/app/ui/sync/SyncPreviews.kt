package com.memebrowser.app.ui.sync

import androidx.compose.runtime.Composable
import androidx.compose.ui.tooling.preview.Preview
import com.memebrowser.app.data.album.ScanState
import com.memebrowser.app.data.sync.SyncState
import com.memebrowser.app.data.sync.SyncSummary
import com.memebrowser.app.data.tracker.AlbumSelection
import com.memebrowser.app.data.tracker.TrackerCounts
import com.memebrowser.app.ui.theme.MemeBrowserTheme

private val previewState = SyncUiState(
    loaded = true,
    environment = EnvironmentInfo("general", "General"),
    album = AlbumSelection("content://x/tree/primary%3ADCIM%2FCamera", "Camera"),
    counts = TrackerCounts(uploaded = 40, supported = 120, rejected = 3, pending = 77),
    scan = ScanState.Done(1_700_000_000_000),
)

@Preview(showBackground = true)
@Composable
private fun SyncIdlePreview() = MemeBrowserTheme { SyncContent(previewState, SyncActions()) }

@Preview(showBackground = true, fontScale = 2f)
@Composable
private fun SyncRunningLargeTextPreview() = MemeBrowserTheme {
    SyncContent(previewState.copy(sync = SyncState.Running(1, 2, 50, SyncSummary(uploaded = 10))), SyncActions())
}

@Preview(showBackground = true)
@Composable
private fun SyncAccessLostPreview() = MemeBrowserTheme {
    SyncContent(previewState.copy(scan = ScanState.PermissionLost), SyncActions())
}
