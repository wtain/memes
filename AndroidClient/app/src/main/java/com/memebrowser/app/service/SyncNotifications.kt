package com.memebrowser.app.service

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationCompat
import com.memebrowser.app.MainActivity
import com.memebrowser.app.data.sync.SyncNotificationModel

/** Channel, builders and PendingIntents of the album-sync notifications (A6). Content comes from `SyncNotificationMapper`. */
object SyncNotifications {
    const val CHANNEL_ID = "album_sync"
    private const val CHANNEL_NAME = "Album sync"

    /** Intent extra read by `MainActivity`: the route to open. */
    const val EXTRA_ROUTE = "route"
    const val ROUTE_SYNC = "sync"

    const val TIMEOUT_TEXT = "Android paused the sync after 6 hours. Tap to continue."

    /** A framework drawable: the project has no monochrome icon and no new image assets are added for A6. */
    private val SMALL_ICON = android.R.drawable.stat_sys_upload

    fun ensureChannel(context: Context) {
        val manager = context.getSystemService(NotificationManager::class.java)
        if (manager.getNotificationChannel(CHANNEL_ID) != null) return
        val channel = NotificationChannel(CHANNEL_ID, CHANNEL_NAME, NotificationManager.IMPORTANCE_LOW).apply {
            setShowBadge(false)
        }
        manager.createNotificationChannel(channel)
    }

    /** The notification of one environment. Ongoing ones carry Stop when allowed; finished ones are dismissible. */
    fun build(context: Context, environmentId: String, model: SyncNotificationModel): Notification {
        val builder = base(context)
            .setContentTitle(model.title)
            .setContentText(model.text)
            .setOngoing(model.ongoing)
            .setAutoCancel(!model.ongoing)
        model.progress?.let { builder.setProgress(it.total, it.done.coerceAtMost(it.total), false) }
        if (model.canStop) {
            builder.addAction(0, "Stop", stopIntent(context, environmentId))
        }
        return builder.build()
    }

    /** What the service shows until the first real content is known (also for a start with nothing to show). */
    fun placeholder(context: Context): Notification =
        base(context).setContentTitle("Album sync").setContentText("Starting...").setOngoing(true).build()

    /** The final notification of an environment whose run Android ended at the 6 h foreground-service cap. */
    fun timedOut(context: Context, environmentName: String): Notification =
        base(context)
            .setContentTitle("$environmentName: sync paused")
            .setContentText(TIMEOUT_TEXT)
            .setStyle(NotificationCompat.BigTextStyle().bigText(TIMEOUT_TEXT))
            .setOngoing(false)
            .setAutoCancel(true)
            .build()

    private fun base(context: Context): NotificationCompat.Builder =
        NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(SMALL_ICON)
            .setCategory(NotificationCompat.CATEGORY_PROGRESS)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setOnlyAlertOnce(true)
            .setSilent(true)
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
            .setContentIntent(contentIntent(context))

    private fun contentIntent(context: Context): PendingIntent {
        val intent = Intent(context, MainActivity::class.java)
            .putExtra(EXTRA_ROUTE, ROUTE_SYNC)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP)
        return PendingIntent.getActivity(
            context, 0, intent, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
    }

    /** One request code per environment, so each Stop action keeps its own extra. */
    private fun stopIntent(context: Context, environmentId: String): PendingIntent {
        val intent = Intent(context, SyncService::class.java)
            .setAction(SyncService.ACTION_STOP)
            .putExtra(SyncService.EXTRA_ENVIRONMENT_ID, environmentId)
        return PendingIntent.getService(
            context,
            SyncNotificationPlan.notificationId(environmentId),
            intent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
    }
}
