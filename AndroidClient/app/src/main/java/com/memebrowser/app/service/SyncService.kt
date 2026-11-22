package com.memebrowser.app.service

import android.app.Notification
import android.app.NotificationManager
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.IBinder
import android.util.Log
import androidx.core.app.ServiceCompat
import com.memebrowser.app.data.album.AlbumSyncCoordinator
import com.memebrowser.app.data.store.EnvironmentStore
import com.memebrowser.app.data.sync.SyncEngine
import com.memebrowser.app.data.sync.SyncNotificationMapper
import com.memebrowser.app.data.sync.SyncState
import com.memebrowser.app.data.sync.WaitReason
import com.memebrowser.app.data.sync.isActive
import com.memebrowser.app.data.tracker.TrackerCounts
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.catch
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.onEach
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.flow.transformLatest
import kotlinx.coroutines.launch
import javax.inject.Inject

/**
 * Keeps the process alive while album syncs run and shows one notification per environment (A6). The engine owns the
 * work; this service only renders [SyncEngine] state through [SyncNotificationMapper].
 *
 * Everything runs on the main thread (scope on `Dispatchers.Main.immediate`), so the bookkeeping needs no locks.
 *  - Every start that came from `startForegroundService` calls `startForeground` at once. A start with nothing active
 *    (the run ended before the start was delivered) shows the placeholder only for that call and stops.
 *  - The foreground notification is the oldest running environment's ([SyncNotificationPlan.foreground]); the others
 *    are posted with `notify`. When the foreground environment ends, the next one is promoted with `startForeground`.
 *  - An environment is followed until ITS state flow shows a terminal state (not until it leaves the active set, which
 *    can be seen first), then its dismissible terminal notification is posted. With nothing left the service detaches
 *    its notification (`STOP_FOREGROUND_DETACH`, so the terminal ones stay) and stops itself.
 *  - [ACTION_STOP] (the notification's Stop action) calls [SyncEngine.stop] for [EXTRA_ENVIRONMENT_ID].
 *  - Android 15's 6 h cap ([onTimeout]): every run is stopped and gets the "paused" notification.
 */
@AndroidEntryPoint
class SyncService : Service() {
    @Inject lateinit var engine: SyncEngine
    @Inject lateinit var coordinator: AlbumSyncCoordinator
    @Inject lateinit var environmentStore: EnvironmentStore

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private lateinit var names: StateFlow<Map<String, String>>
    private val manager: NotificationManager by lazy { getSystemService(NotificationManager::class.java) }

    /** Followed environments in start order (oldest first) and their collector jobs. */
    private val tracked = LinkedHashMap<String, Job>()

    /** The last ongoing notification of each followed environment (used when it is promoted to foreground). */
    private val latest = HashMap<String, Notification>()
    private var foregroundEnv: String? = null
    private var lastStartId = 0
    private var watching = false
    private var timedOut = false

    private data class Snapshot(val state: SyncState, val counts: TrackerCounts, val name: String)

    override fun onCreate() {
        super.onCreate()
        SyncNotifications.ensureChannel(this)
        names = environmentStore.environments
            .map { list -> list.associate { it.id to it.name } }
            .catch { e -> Log.w(TAG, "Could not read the environment names", e); emit(emptyMap()) }
            .stateIn(scope, SharingStarted.Eagerly, emptyMap())
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        lastStartId = startId
        if (intent?.action == ACTION_STOP) {
            intent.getStringExtra(EXTRA_ENVIRONMENT_ID)?.let { engine.stop(it) }
            // A plain start (not a foreground one): nothing to promote. Its collector posts the result.
            if (tracked.isEmpty()) stopSelf(startId)
            return START_NOT_STICKY
        }
        timedOut = false
        val active = engine.activeEnvironments.value
        if (tracked.isEmpty() && active.isEmpty()) {
            // The run ended before this start arrived: satisfy startForegroundService, then go away without a trace.
            promote(SyncNotificationPlan.PLACEHOLDER_ID, SyncNotifications.placeholder(this))
            ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
            stopSelf(startId)
            return START_NOT_STICKY
        }
        val fg = foregroundEnv
            ?: SyncNotificationPlan.foreground(SyncNotificationPlan.order(tracked.keys.toList(), active), null)!!
        foregroundEnv = fg
        promote(SyncNotificationPlan.notificationId(fg), latest[fg] ?: SyncNotifications.placeholder(this))
        watch()
        reconcile()
        return START_NOT_STICKY
    }

    override fun onTimeout(startId: Int) = handleTimeout()

    override fun onTimeout(startId: Int, fgsType: Int) = handleTimeout()

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }

    // ---- bookkeeping (main thread) -------------------------------------------------------------------------

    private fun watch() {
        if (watching) return
        watching = true
        scope.launch { engine.activeEnvironments.collect { reconcile() } }
    }

    /** Follows every active environment not followed yet, in [SyncNotificationPlan.order]. */
    private fun reconcile() {
        if (timedOut) return
        val active = engine.activeEnvironments.value
        SyncNotificationPlan.order(tracked.keys.toList(), active)
            .filter { it in active && it !in tracked }
            .forEach { track(it) }
    }

    @OptIn(ExperimentalCoroutinesApi::class)
    private fun track(env: String) {
        val counts = coordinator.counts(env).catch { e ->
            Log.w(TAG, "Could not read the tracker counts", e)
            emit(TrackerCounts.EMPTY)
        }
        tracked[env] = scope.launch {
            try {
                val end = combine(engine.state(env), counts, names) { s, c, n -> Snapshot(s, c, n[env] ?: env) }
                    .transformLatest { snap ->
                        emit(snap)
                        // The "Retrying in N s" countdown needs a fresh render every second.
                        val s = snap.state
                        if (s is SyncState.Waiting && s.reason == WaitReason.RETRY_BACKOFF) {
                            while (true) {
                                delay(1_000)
                                emit(snap)
                            }
                        }
                    }
                    .onEach { snap -> if (snap.state.isActive) renderOngoing(env, snap) }
                    .first { !it.state.isActive && it.state != SyncState.Idle }
                finish(env, end)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                Log.w(TAG, "Sync notification for one environment failed", e)
                finish(env, null)
            }
        }
    }

    private fun renderOngoing(env: String, snap: Snapshot) {
        val model = SyncNotificationMapper.map(snap.name, snap.state, snap.counts, System.currentTimeMillis()) ?: return
        val notification = SyncNotifications.build(this, env, model)
        latest[env] = notification
        post(SyncNotificationPlan.notificationId(env), notification)
    }

    /** [env]'s run ended ([end] its terminal snapshot, null after an internal failure). */
    private fun finish(env: String, end: Snapshot?) {
        tracked.remove(env)
        latest.remove(env)
        reconcile() // a run that started meanwhile (possibly of the same environment) is followed right away
        if (env == foregroundEnv) {
            val next = SyncNotificationPlan.foreground(tracked.keys.toList(), null)
            foregroundEnv = next
            if (next != null) {
                promote(SyncNotificationPlan.notificationId(next), latest[next] ?: SyncNotifications.placeholder(this))
            } else {
                ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_DETACH)
            }
        }
        // Not when the same environment already runs again: its new run owns the notification id.
        if (end != null && env !in tracked) {
            SyncNotificationMapper.map(end.name, end.state, end.counts, System.currentTimeMillis())?.let {
                post(SyncNotificationPlan.notificationId(env), SyncNotifications.build(this, env, it))
            }
        }
        if (tracked.isEmpty()) stopSelf(lastStartId) // refused when a newer start is pending; that start takes over
    }

    private fun handleTimeout() {
        if (timedOut) return
        timedOut = true
        val envs = tracked.keys.toList()
        tracked.values.forEach { it.cancel() }
        tracked.clear()
        latest.clear()
        foregroundEnv = null
        envs.forEach { engine.stop(it) }
        ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_DETACH)
        envs.forEach { env ->
            post(SyncNotificationPlan.notificationId(env), SyncNotifications.timedOut(this, names.value[env] ?: env))
        }
        stopSelf()
    }

    // ---- platform calls (never crash the service) ----------------------------------------------------------

    private fun promote(id: Int, notification: Notification) {
        try {
            ServiceCompat.startForeground(this, id, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        } catch (e: IllegalStateException) { // ForegroundServiceStartNotAllowedException is one
            Log.w(TAG, "Could not go foreground", e)
        } catch (e: SecurityException) {
            Log.w(TAG, "Could not go foreground", e)
        }
    }

    private fun post(id: Int, notification: Notification) {
        try {
            manager.notify(id, notification)
        } catch (e: SecurityException) { // POST_NOTIFICATIONS denied on some vendors throws instead of dropping
            Log.w(TAG, "Could not post the sync notification", e)
        }
    }

    companion object {
        const val ACTION_STOP = "com.memebrowser.app.action.STOP_SYNC"
        const val EXTRA_ENVIRONMENT_ID = "environmentId"
        private const val TAG = "SyncService"
    }
}
