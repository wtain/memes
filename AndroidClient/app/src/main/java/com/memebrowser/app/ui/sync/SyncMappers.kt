package com.memebrowser.app.ui.sync

import com.memebrowser.app.data.album.ChangeAlbumPlan
import com.memebrowser.app.data.album.ScanFailure
import com.memebrowser.app.data.album.ScanState
import com.memebrowser.app.data.sync.SyncEngine
import com.memebrowser.app.data.sync.SyncError
import com.memebrowser.app.data.sync.SyncState
import com.memebrowser.app.data.sync.SyncSummary
import com.memebrowser.app.data.sync.WaitReason
import com.memebrowser.app.data.sync.isActive
import com.memebrowser.app.data.tracker.RejectionReasons
import com.memebrowser.app.data.tracker.SupportedImages
import com.memebrowser.app.data.tracker.TrackerCounts
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** Pure mapping of tracker, scan and sync state to what the sync screen shows. No Android types, unit-tested. */

/** Formats an epoch-ms instant as a wall-clock time; injected so tests do not depend on the time zone. */
typealias TimeFormatter = (Long) -> String

val DefaultTimeFormatter: TimeFormatter = { at ->
    SimpleDateFormat("HH:mm:ss", Locale.getDefault()).format(Date(at))
}

/** X / Y as a bar fraction in 0..1; Y = 0 gives 0 (never NaN), X above Y (deleted-but-uploaded files) is capped. */
fun progressFraction(counts: TrackerCounts): Float =
    if (counts.supported <= 0) 0f else (counts.uploaded.toFloat() / counts.supported).coerceIn(0f, 1f)

fun SyncState.isTerminal(): Boolean = this is SyncState.Finished || this is SyncState.Stopped || this is SyncState.Failed

/** Everything the buttons of the screen depend on, derived in one pure place. */
data class SyncControls(
    val canChooseAlbum: Boolean,
    val canSync: Boolean,
    val canStop: Boolean,
    val canRefresh: Boolean,
    val canChangeAlbum: Boolean,
    val batchSizeAppliesNextBatch: Boolean,
)

fun syncControls(hasAlbum: Boolean, counts: TrackerCounts, scan: ScanState, sync: SyncState, busy: Boolean): SyncControls {
    val running = sync.isActive
    val stopping = sync is SyncState.Stopping
    val scanning = scan == ScanState.Scanning
    val granted = scan != ScanState.PermissionLost
    return SyncControls(
        canChooseAlbum = !hasAlbum && !busy,
        canSync = hasAlbum && granted && counts.pending > 0 && !running && !busy,
        canStop = running && !stopping,
        canRefresh = hasAlbum && !scanning && !busy,
        canChangeAlbum = hasAlbum && !busy,
        batchSizeAppliesNextBatch = running,
    )
}

/** One line for the scan, or null when there is nothing to say (idle). */
fun scanStatusText(scan: ScanState, format: TimeFormatter = DefaultTimeFormatter): String? = when (scan) {
    ScanState.Idle -> null
    ScanState.Scanning -> "Scanning the album..."
    is ScanState.Done -> "Last refreshed at ${format(scan.at)}"
    ScanState.PermissionLost -> "Access to the album was lost. Choose the album again to continue."
    is ScanState.Failed -> when (scan.reason) {
        ScanFailure.NO_ALBUM -> "No album is selected."
        ScanFailure.LISTING_INCOMPLETE ->
            "The album could not be listed completely (still loading or unavailable, for example a removed card). Counts are unchanged; try Refresh again."
        ScanFailure.TRACKER_ERROR -> "Could not update the list of files. Try Refresh again."
        ScanFailure.CANCELLED -> "The refresh was interrupted. Try Refresh again."
    }
}

/** True when the scan state is a problem the user should see highlighted (the text is still always shown). */
fun scanIsProblem(scan: ScanState): Boolean = scan is ScanState.Failed || scan == ScanState.PermissionLost

/** Actionable text for every [SyncError]. [engineMessage] is the engine's own detail (used where it adds information). */
fun syncErrorMessage(error: SyncError, engineMessage: String = ""): String = when (error) {
    SyncError.NETWORK -> "No answer from the server. Check the connection and the server address, then sync again. Unsent files stay pending."
    SyncError.SERVER ->
        if (engineMessage.contains("backend too old", ignoreCase = true)) {
            "The server is too old for album sync. Update the backend, then sync again."
        } else {
            "The server failed while receiving files. Try again later; unsent files stay pending."
        }
    SyncError.INBOX_FULL_OR_UNWRITABLE -> "The server inbox is full or not writable - run ingestion or check the server, then sync again."
    SyncError.DISK_LOW -> "The server is almost out of disk space. Free some space on the server, then sync again."
    SyncError.RATE_LIMITED -> "The server keeps asking us to slow down. Wait a few minutes, then sync again."
    SyncError.UNEXPECTED -> "Something unexpected went wrong" + engineDetail(engineMessage) + ". Unsent files stay pending; try again."
    SyncError.NO_ALBUM -> "No album is selected. Choose an album folder first."
    SyncError.PERMISSION_LOST -> "Access to the album was lost. Choose the album again."
    SyncError.FILE_READ_ERROR ->
        "A file in the album could not be read (it may be on a removed card or not downloaded yet). Nothing was rejected; sync again later."
}

private fun engineDetail(message: String): String = if (message.isBlank()) "" else " ($message)"

/** Errors where the fix is to pick the album again (shows the same action as the lost-access state). */
fun syncErrorNeedsRepick(error: SyncError): Boolean = error == SyncError.PERMISSION_LOST || error == SyncError.NO_ALBUM

fun summaryText(summary: SyncSummary): String {
    val parts = mutableListOf("${summary.uploaded} uploaded")
    if (summary.duplicates > 0) parts += "${summary.duplicates} already on the server"
    if (summary.rejected > 0) parts += "${summary.rejected} rejected"
    return parts.joinToString(", ")
}

/** Headline of the sync state, null for Idle. Failed uses [syncErrorMessage]. */
fun syncStatusText(sync: SyncState, format: TimeFormatter = DefaultTimeFormatter): String? = when (sync) {
    SyncState.Idle -> null
    is SyncState.Running ->
        if (sync.batchNumber <= 0) "Starting..." else "Sending batch ${sync.batchNumber} (${sync.currentBatchSize} files)... ${summaryText(sync.summary)} so far"
    is SyncState.Waiting -> when (sync.reason) {
        WaitReason.RATE_LIMITED -> "Server asked us to wait, resuming at ${format(sync.resumeAt)}"
        WaitReason.PACING -> "Pacing requests to stay under the server limit, resuming at ${format(sync.resumeAt)}"
    }
    is SyncState.Stopping -> "Stopping..."
    is SyncState.Finished -> "Finished: ${summaryText(sync.summary)}"
    is SyncState.Stopped -> "Stopped: ${summaryText(sync.summary)}. Unsent files stay pending."
    is SyncState.Failed -> syncErrorMessage(sync.error, sync.message)
}

/** Human text of a rejection reason code (local codes, `server_error`, or the backend's own reason text). */
fun rejectionReasonText(reason: String?): String = when {
    reason.isNullOrBlank() -> "Rejected"
    reason == SupportedImages.REASON_UNSUPPORTED_TYPE -> "Not a supported image type"
    reason == SupportedImages.REASON_TOO_LARGE -> "Larger than 20 MB"
    reason == SyncEngine.REASON_FILE_UNAVAILABLE -> "The file is no longer available on the phone"
    reason == RejectionReasons.SERVER_ERROR -> "The server failed on this file"
    reason.startsWith("Unsupported file type") || reason.startsWith("Unsupported file extension") ->
        "Not a supported image type (server)"
    reason.startsWith("File too large") -> "Larger than the server limit"
    reason.startsWith("File content does not match") -> "The content is not a valid image"
    else -> reason // an unknown backend reason is shown as it came
}

/** A dialog line starting with this is shown as a problem (error colour plus icon). */
const val WARNING_PREFIX = "Warning:"

/** The lines of the change-album confirmation. [syncRunning]: a running sync will be stopped by confirming. */
fun changeAlbumDialogLines(plan: ChangeAlbumPlan, syncRunning: Boolean): List<String> = buildList {
    val oldName = plan.currentAlbum?.displayName?.ifBlank { null } ?: "the current album"
    add("Change from \"$oldName\" to \"${plan.newDisplayName}\"?")
    add(
        "This resets the upload list of this environment: ${plan.uploaded} uploaded, ${plan.pending} pending and " +
            "${plan.rejected} rejected entries are forgotten. Files already on the server stay there; the new album is scanned from scratch " +
            "and files that are already on the server are reported as duplicates.",
    )
    if (plan.looksLikeAppOwnFolder) {
        add("$WARNING_PREFIX this looks like a folder this app saves memes into. Syncing it would send memes back to the server.")
    }
    if (plan.needsRegrant) add("Access to this folder will be granted again.")
    if (syncRunning) add("The running sync will be stopped.")
}

/** Only `server_error` can be retried; every other rejection is permanent. */
fun canRetry(reason: String?): Boolean = reason == RejectionReasons.SERVER_ERROR
