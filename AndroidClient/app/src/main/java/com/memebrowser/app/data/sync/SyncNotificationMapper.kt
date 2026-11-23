package com.memebrowser.app.data.sync

import com.memebrowser.app.data.tracker.TrackerCounts
import com.memebrowser.app.ui.sync.syncErrorMessage

data class NotificationProgress(val done: Int, val total: Int)

data class SyncNotificationModel(
    val title: String,
    val text: String,
    val progress: NotificationProgress?,
    val ongoing: Boolean,
    val canStop: Boolean,
)

/** Pure mapping from sync state to notification content (no Android types). Idle -> null (no notification). */
object SyncNotificationMapper {
    fun map(environmentName: String, state: SyncState, counts: TrackerCounts, now: Long): SyncNotificationModel? {
        val progress = if (counts.supported > 0) NotificationProgress(counts.uploaded + counts.rejected, counts.supported) else null
        val countText = "${counts.uploaded} of ${counts.supported} uploaded"
        val syncing = "$environmentName: syncing"
        return when (state) {
            SyncState.Idle -> null
            is SyncState.Running -> SyncNotificationModel(syncing, countText, progress, ongoing = true, canStop = true)
            is SyncState.Waiting -> SyncNotificationModel(syncing, waitText(state, now), progress, ongoing = true, canStop = true)
            is SyncState.Stopping -> SyncNotificationModel(syncing, "Stopping...", progress, ongoing = true, canStop = false)
            is SyncState.Finished -> SyncNotificationModel(
                "$environmentName: sync finished, ${state.summary.uploaded} uploaded", countText, null, ongoing = false, canStop = false,
            )
            is SyncState.Stopped -> SyncNotificationModel("$environmentName: sync stopped", countText, null, ongoing = false, canStop = false)
            is SyncState.Failed -> SyncNotificationModel(
                "$environmentName: sync failed", syncErrorMessage(state.error, state.message), null, ongoing = false, canStop = false,
            )
        }
    }

    private fun waitText(state: SyncState.Waiting, now: Long): String = when (state.reason) {
        WaitReason.NEEDS_WIFI -> "Waiting for Wi-Fi"
        WaitReason.NO_NETWORK -> "Waiting for network"
        WaitReason.RETRY_BACKOFF -> {
            val seconds = maxOf(0L, Math.floorDiv(state.resumeAt - now + 999, 1000L))
            "Retrying in $seconds s (attempt ${state.attempt} of ${SyncEngine.MAX_RETRY_ATTEMPTS})"
        }
        WaitReason.PACING, WaitReason.RATE_LIMITED -> "Pausing to respect the server limit"
    }
}
