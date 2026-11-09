package com.memebrowser.app.ui.sync

import android.util.Log
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.memebrowser.app.data.album.AlbumChangeResult
import com.memebrowser.app.data.album.AlbumSyncCoordinator
import com.memebrowser.app.data.album.ChangeAlbumPlan
import com.memebrowser.app.data.album.ScanState
import com.memebrowser.app.data.repository.EnvironmentRepository
import com.memebrowser.app.data.sync.SyncEngine
import com.memebrowser.app.data.sync.SyncState
import com.memebrowser.app.data.tracker.AlbumSelection
import com.memebrowser.app.data.tracker.TrackerCounts
import com.memebrowser.app.data.tracker.TrackerRepository
import com.memebrowser.app.data.tracker.TrackerSettingsStore
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.flatMapLatest
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.flow.flowOn
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.onStart
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

private const val TAG = "SyncViewModel"

/** The environment the screen is bound to (the app's selected one). */
data class EnvironmentInfo(val id: String, val name: String)

/** One row of the rejected list, already mapped to text. */
data class RejectedItem(
    val albumTreeUri: String,
    val documentId: String,
    val displayName: String,
    val reasonText: String,
    val canRetry: Boolean,
)

/** The one immutable state the screen renders. */
data class SyncUiState(
    /** False until the first environment and tracker values arrived (the screen shows a spinner). */
    val loaded: Boolean = false,
    /** Null while [loaded] means no environment is selected (the screen shows an error). */
    val environment: EnvironmentInfo? = null,
    val album: AlbumSelection? = null,
    val counts: TrackerCounts = TrackerCounts.EMPTY,
    val scan: ScanState = ScanState.Idle,
    /** The engine state, with a dismissed terminal state shown as Idle. */
    val sync: SyncState = SyncState.Idle,
    val batchSize: Int = TrackerSettingsStore.DEFAULT_BATCH_SIZE,
    val rejectedVisible: Boolean = false,
    /** Filled only while [rejectedVisible] (the list can be thousands of rows). */
    val rejected: List<RejectedItem> = emptyList(),
    /** The rejected list is open but its first emission has not arrived (so "No rejected files." must not show yet). */
    val rejectedLoading: Boolean = false,
    /** The change-album confirmation, kept here so it survives rotation (the picker's grant lives in this process). */
    val changePlan: ChangeAlbumPlan? = null,
    /** A one-shot message for a snackbar; the screen calls [SyncViewModel.dismissMessage] after showing it. */
    val message: String? = null,
    /** An album pick is being processed. */
    val busy: Boolean = false,
) {
    val controls: SyncControls get() = syncControls(album != null, counts, scan, sync, busy)
    val retryableCount: Int get() = rejected.count { it.canRetry }
}

@OptIn(ExperimentalCoroutinesApi::class)
@HiltViewModel
class SyncViewModel internal constructor(
    environments: EnvironmentRepository,
    private val coordinator: AlbumSyncCoordinator,
    private val engine: SyncEngine,
    private val tracker: TrackerRepository,
    private val clock: () -> Long,
    /** Where the rejected rows are mapped to text (thousands of rows must not be mapped on Main). */
    private val mappingDispatcher: CoroutineDispatcher,
) : ViewModel() {

    @Inject
    constructor(
        environments: EnvironmentRepository,
        coordinator: AlbumSyncCoordinator,
        engine: SyncEngine,
        tracker: TrackerRepository,
    ) : this(environments, coordinator, engine, tracker, System::currentTimeMillis, Dispatchers.Default)

    /** Screen-local state that is not derived from the data layer. One per environment, so nothing leaks across a switch. */
    private data class Local(
        val plan: ChangeAlbumPlan? = null,
        val message: String? = null,
        val busy: Boolean = false,
        val dismissedSync: SyncState? = null,
        val rejectedVisible: Boolean = false,
    )

    private class Snapshot(
        val env: EnvironmentInfo,
        val album: AlbumSelection?,
        val counts: TrackerCounts,
        val scan: ScanState,
        val sync: SyncState,
        /** Null while the list is open and not yet loaded. */
        val rejected: List<RejectedItem>?,
    )

    private val locals = MutableStateFlow<Map<String, Local>>(emptyMap())
    private val autoRefreshAt = HashMap<String, Long>() // main thread only

    private fun localOf(env: String): Local = locals.value[env] ?: Local()

    private fun updateLocal(env: String, change: (Local) -> Local) = locals.update { it + (env to change(it[env] ?: Local())) }

    /** Id and name come from ONE emission, so a switch never shows the new id with the old name. */
    private val selectedEnvironment: Flow<EnvironmentInfo?> =
        environments.environmentsWithSelection
            .map { list -> list.firstOrNull { it.isSelected }?.environment?.let { EnvironmentInfo(it.id, it.name) } }
            .distinctUntilChanged()

    private fun snapshotOf(env: EnvironmentInfo): Flow<Snapshot?> {
        val rejected: Flow<List<RejectedItem>?> = locals.map { localOf(env.id).rejectedVisible }.distinctUntilChanged().flatMapLatest { visible ->
            if (visible) {
                coordinator.rejectedFiles(env.id)
                    .map<_, List<RejectedItem>?> { rows ->
                        rows.map {
                            RejectedItem(it.albumTreeUri, it.documentId, it.displayName, rejectionReasonText(it.rejectionReason), canRetry(it.rejectionReason))
                        }
                    }
                    .flowOn(mappingDispatcher)
                    .onStart { emit(null) }
            } else {
                flowOf(emptyList())
            }
        }
        return combine(
            coordinator.album(env.id),
            coordinator.counts(env.id),
            coordinator.scanState(env.id),
            engine.state(env.id),
            rejected,
        ) { album, counts, scan, sync, rows -> Snapshot(env, album, counts, scan, sync, rows) }
    }

    /** Every environment switch re-binds all of it (flatMapLatest): nothing from the old environment survives. */
    val uiState: StateFlow<SyncUiState> =
        combine(
            selectedEnvironment.flatMapLatest { env -> if (env == null) flowOf(null) else snapshotOf(env) },
            tracker.batchSize(),
            locals,
        ) { snap, batch, all ->
            if (snap == null) {
                SyncUiState(loaded = true, batchSize = batch)
            } else {
                val l = all[snap.env.id] ?: Local()
                // The dialog belongs to this environment and closes as soon as the album change is committed (the stored
                // album is no longer the one the plan was built from), while the rescan that follows shows as scan state.
                val plan = l.plan?.takeIf { it.environmentId == snap.env.id && snap.album?.treeUri == it.currentAlbum?.treeUri }
                SyncUiState(
                    loaded = true,
                    environment = snap.env,
                    album = snap.album,
                    counts = snap.counts,
                    scan = snap.scan,
                    sync = if (snap.sync.isTerminal() && snap.sync == l.dismissedSync) SyncState.Idle else snap.sync,
                    batchSize = batch,
                    rejectedVisible = l.rejectedVisible,
                    rejected = snap.rejected.orEmpty(),
                    rejectedLoading = l.rejectedVisible && snap.rejected == null,
                    changePlan = plan,
                    message = l.message,
                    busy = l.busy,
                )
            }
        }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), SyncUiState())

    /**
     * The environment the user is looking at. Every action acts on THIS id (the one of the state on screen), never on one
     * resolved later, so a tap during an environment switch cannot land on the other environment.
     */
    private fun shownEnvironment(): String? = uiState.value.environment?.id

    // ---- screen lifecycle ------------------------------------------------------------------------------------

    /**
     * The screen was shown (open, resume or an environment switch). Refreshes the album once: repeated calls for the same
     * environment within [AUTO_REFRESH_MIN_INTERVAL_MS] (rotation, a double start, a recomposition) do nothing. The
     * throttle slot is only used up when a refresh really ran and did not fail: no album, a running scan or a failed
     * listing leave it free.
     */
    fun onScreenStarted() {
        val env = shownEnvironment() ?: return
        val now = clock()
        val last = autoRefreshAt[env]
        if (last != null && now - last in 0 until AUTO_REFRESH_MIN_INTERVAL_MS) return
        if (uiState.value.album == null) return
        if (coordinator.scanState(env).value == ScanState.Scanning) return
        autoRefreshAt[env] = now // before the first suspension, so a double start is absorbed
        viewModelScope.launch {
            val result = guarded(env) { coordinator.refreshScan(env) }
            if (result == null || result is ScanState.Failed) autoRefreshAt.remove(env, now)
        }
    }

    fun refresh() {
        val env = shownEnvironment() ?: return
        if (coordinator.scanState(env).value == ScanState.Scanning) return
        viewModelScope.launch { guarded(env) { coordinator.refreshScan(env) } }
    }

    // ---- album -----------------------------------------------------------------------------------------------

    /**
     * The folder picker returned [treeUri]. Handles all three entries: the first selection, "Choose album again" after a
     * lost grant (the same tree: the coordinator re-takes the grant and rescans, no wipe) and "Change album" (another
     * tree: a confirmation first).
     */
    fun onAlbumPicked(treeUri: String) {
        val env = shownEnvironment() ?: return
        if (localOf(env).busy) return
        updateLocal(env) { it.copy(busy = true) } // before launching: a second tap is dropped even if the launch is deferred
        viewModelScope.launch {
            try {
                guarded(env) {
                    if (coordinator.album(env).first() == null) {
                        handle(coordinator.selectAlbum(env, treeUri), env, treeUri, applied = null, depth = 0)
                    } else {
                        requestChange(env, treeUri, depth = 0)
                    }
                }
            } finally {
                updateLocal(env) { it.copy(busy = false) }
            }
        }
    }

    /** The user confirmed the dialog. */
    fun confirmChangeAlbum() {
        val env = shownEnvironment() ?: return
        val plan = localOf(env).plan ?: return
        if (localOf(env).busy) return
        updateLocal(env) { it.copy(busy = true) }
        viewModelScope.launch {
            try {
                guarded(env) { handle(coordinator.confirmChangeAlbum(plan), env, plan.newTreeUri, applied = MSG_ALBUM_CHANGED, depth = 0) }
            } finally {
                updateLocal(env) { it.copy(busy = false) }
            }
        }
    }

    fun dismissChangeAlbum() {
        val env = shownEnvironment() ?: return
        updateLocal(env) { if (it.busy) it else it.copy(plan = null) }
    }

    private suspend fun requestChange(env: String, treeUri: String, note: String? = null, depth: Int) {
        val plan = coordinator.requestChangeAlbum(env, treeUri)
        when {
            plan.isNoOp -> updateLocal(env) { it.copy(plan = null, message = MSG_SAME_ALBUM) }
            // First selection, or the same album with a lost grant: nothing is wiped, so no confirmation.
            !plan.wipesData -> handle(coordinator.confirmChangeAlbum(plan), env, treeUri, applied = null, depth = depth)
            else -> updateLocal(env) { it.copy(plan = plan, message = note ?: it.message) }
        }
    }

    /** [depth]: how many times the plan was rebuilt automatically; the second stale answer ends in a message, not a loop. */
    private suspend fun handle(result: AlbumChangeResult, env: String, treeUri: String, applied: String?, depth: Int) {
        when (result) {
            is AlbumChangeResult.Applied -> updateLocal(env) { it.copy(plan = null, message = applied) }
            AlbumChangeResult.Unchanged -> updateLocal(env) { it.copy(plan = null, message = MSG_SAME_ALBUM) }
            AlbumChangeResult.GrantFailed -> updateLocal(env) { it.copy(plan = null, message = MSG_GRANT_FAILED) }
            AlbumChangeResult.Failed -> updateLocal(env) { it.copy(plan = null, message = MSG_CHANGE_FAILED) }
            AlbumChangeResult.RequiresConfirmation ->
                if (depth >= MAX_PLAN_REBUILDS) giveUpOnPlan(env) else requestChange(env, treeUri, depth = depth + 1)
            AlbumChangeResult.PlanStale ->
                if (depth >= MAX_PLAN_REBUILDS) giveUpOnPlan(env) else requestChange(env, treeUri, note = MSG_PLAN_STALE, depth = depth + 1)
        }
    }

    private fun giveUpOnPlan(env: String) = updateLocal(env) { it.copy(plan = null, message = MSG_PLAN_KEEPS_CHANGING) }

    // ---- sync ------------------------------------------------------------------------------------------------

    fun startSync() {
        val env = shownEnvironment() ?: return
        updateLocal(env) { it.copy(dismissedSync = null) }
        engine.start(env)
    }

    fun stopSync() {
        val env = shownEnvironment() ?: return
        engine.stop(env)
    }

    /** Hides a finished/stopped/failed banner until the next run ends. */
    fun dismissSyncResult() {
        val env = shownEnvironment() ?: return
        val state = engine.state(env).value
        if (state.isTerminal()) updateLocal(env) { it.copy(dismissedSync = state) }
    }

    fun setBatchSize(size: Int) {
        val env = shownEnvironment() ?: return
        viewModelScope.launch { guarded(env) { tracker.setBatchSize(TrackerSettingsStore.clampBatchSize(size)) } }
    }

    // ---- rejected files --------------------------------------------------------------------------------------

    fun showRejected() {
        val env = shownEnvironment() ?: return
        updateLocal(env) { it.copy(rejectedVisible = true) }
    }

    fun hideRejected() {
        val env = shownEnvironment() ?: return
        updateLocal(env) { it.copy(rejectedVisible = false) }
    }

    fun retryServerErrors() {
        val env = shownEnvironment() ?: return
        viewModelScope.launch {
            guarded(env) {
                val n = tracker.retryServerErrors(env)
                post(env, if (n > 0) "${fileCount(n)} will be sent again on the next sync" else "No files with server errors")
            }
        }
    }

    fun retryServerError(item: RejectedItem) {
        val env = shownEnvironment() ?: return
        viewModelScope.launch {
            guarded(env) {
                val ok = tracker.retryServerError(env, item.albumTreeUri, item.documentId)
                post(env, if (ok) "${item.displayName} will be sent again on the next sync" else "That file cannot be retried")
            }
        }
    }

    fun dismissMessage() {
        val env = shownEnvironment() ?: return
        updateLocal(env) { it.copy(message = null) }
    }

    // ---- helpers ---------------------------------------------------------------------------------------------

    private fun post(env: String, message: String) = updateLocal(env) { it.copy(message = message) }

    /**
     * Data-layer calls do not throw by contract, but a screen must not crash on a disk or database error. The user sees a
     * generic message; the detail goes to the log (never file contents) and is not shown. Returns null after an exception.
     */
    private suspend fun <T> guarded(env: String, block: suspend () -> T): T? = try {
        block()
    } catch (e: CancellationException) {
        throw e
    } catch (e: Exception) {
        Log.e(TAG, "Unexpected error in the sync screen", e)
        post(env, MSG_UNEXPECTED)
        null
    }

    companion object {
        const val AUTO_REFRESH_MIN_INTERVAL_MS = 30_000L

        /** A stale plan is rebuilt once on its own; a second stale answer stops with a message. */
        const val MAX_PLAN_REBUILDS = 1
        const val MSG_SAME_ALBUM = "That album is already selected."
        const val MSG_GRANT_FAILED = "Could not keep access to that folder. Please choose it again."
        const val MSG_CHANGE_FAILED = "Could not save the album. Nothing was changed; try again."
        const val MSG_PLAN_STALE = "The selected album changed in the meantime. Please review the confirmation again."
        const val MSG_PLAN_KEEPS_CHANGING = "The selected album keeps changing. Nothing was changed; choose the folder again."
        const val MSG_ALBUM_CHANGED = "Album changed. The upload list was reset."
        const val MSG_UNEXPECTED = "Something went wrong. Try again."
    }
}
