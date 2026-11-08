package com.memebrowser.app.ui.sync

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
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

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
    private val environments: EnvironmentRepository,
    private val coordinator: AlbumSyncCoordinator,
    private val engine: SyncEngine,
    private val tracker: TrackerRepository,
    private val clock: () -> Long,
) : ViewModel() {

    @Inject
    constructor(
        environments: EnvironmentRepository,
        coordinator: AlbumSyncCoordinator,
        engine: SyncEngine,
        tracker: TrackerRepository,
    ) : this(environments, coordinator, engine, tracker, System::currentTimeMillis)

    /** Screen-local state that is not derived from the data layer. */
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
        val rejected: List<RejectedItem>,
    )

    private val local = MutableStateFlow(Local())
    private val autoRefreshAt = HashMap<String, Long>() // main thread only

    private val selectedEnvironment: Flow<EnvironmentInfo> =
        combine(environments.selectedEnvironmentId, environments.selectedEnvironmentName) { id, name -> EnvironmentInfo(id, name) }
            .distinctUntilChanged()

    private fun snapshotOf(env: EnvironmentInfo): Flow<Snapshot> {
        val rejected = local.map { it.rejectedVisible }.distinctUntilChanged().flatMapLatest { visible ->
            if (visible) coordinator.rejectedFiles(env.id) else flowOf(emptyList())
        }
        return combine(
            coordinator.album(env.id),
            coordinator.counts(env.id),
            coordinator.scanState(env.id),
            engine.state(env.id),
            rejected,
        ) { album, counts, scan, sync, rows ->
            Snapshot(
                env, album, counts, scan, sync,
                rows.map {
                    RejectedItem(it.albumTreeUri, it.documentId, it.displayName, rejectionReasonText(it.rejectionReason), canRetry(it.rejectionReason))
                },
            )
        }
    }

    /** Every environment switch re-binds all of it (flatMapLatest): nothing from the old environment survives. */
    val uiState: StateFlow<SyncUiState> =
        combine(selectedEnvironment.flatMapLatest { snapshotOf(it) }, tracker.batchSize(), local) { snap, batch, l ->
            val plan = l.plan?.takeIf { it.environmentId == snap.env.id } // a plan never leaks into another environment
            SyncUiState(
                loaded = true,
                environment = snap.env,
                album = snap.album,
                counts = snap.counts,
                scan = snap.scan,
                sync = if (snap.sync.isTerminal() && snap.sync == l.dismissedSync) SyncState.Idle else snap.sync,
                batchSize = batch,
                rejectedVisible = l.rejectedVisible,
                rejected = snap.rejected,
                changePlan = plan,
                message = l.message,
                busy = l.busy,
            )
        }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), SyncUiState())

    // ---- screen lifecycle ------------------------------------------------------------------------------------

    /**
     * The screen was shown (open, resume or an environment switch). Refreshes the album once: repeated calls for the same
     * environment within [AUTO_REFRESH_MIN_INTERVAL_MS] (rotation, a double start, a recomposition) do nothing, and
     * so does a call while a scan is running (the coordinator would only conflate it).
     */
    fun onScreenStarted() {
        viewModelScope.launch {
            val env = currentEnvironmentId()
            val now = clock()
            val last = autoRefreshAt[env]
            if (last != null && now - last in 0 until AUTO_REFRESH_MIN_INTERVAL_MS) return@launch
            autoRefreshAt[env] = now
            if (coordinator.album(env).first() == null) return@launch
            if (coordinator.scanState(env).value == ScanState.Scanning) return@launch
            guarded { coordinator.refreshScan(env) }
        }
    }

    fun refresh() {
        viewModelScope.launch {
            val env = currentEnvironmentId()
            if (coordinator.scanState(env).value == ScanState.Scanning) return@launch
            guarded { coordinator.refreshScan(env) }
        }
    }

    // ---- album -----------------------------------------------------------------------------------------------

    /**
     * The folder picker returned [treeUri]. Handles all three entries: the first selection, "Choose album again" after a
     * lost grant (the same tree: no wipe) and "Change album" (another tree: a confirmation first).
     */
    fun onAlbumPicked(treeUri: String) {
        if (local.value.busy) return
        viewModelScope.launch {
            setBusy(true)
            try {
                guarded {
                    val env = currentEnvironmentId()
                    if (coordinator.album(env).first() == null) {
                        handle(coordinator.selectAlbum(env, treeUri), env, treeUri, applied = null)
                    } else {
                        requestChange(env, treeUri)
                    }
                }
            } finally {
                setBusy(false)
            }
        }
    }

    /** The user confirmed the dialog. */
    fun confirmChangeAlbum() {
        val plan = local.value.plan ?: return
        if (local.value.busy) return
        viewModelScope.launch {
            setBusy(true)
            try {
                guarded { handle(coordinator.confirmChangeAlbum(plan), plan.environmentId, plan.newTreeUri, applied = MSG_ALBUM_CHANGED) }
            } finally {
                setBusy(false)
            }
        }
    }

    fun dismissChangeAlbum() = local.update { if (it.busy) it else it.copy(plan = null) }

    private suspend fun requestChange(env: String, treeUri: String, note: String? = null) {
        val plan = coordinator.requestChangeAlbum(env, treeUri)
        when {
            plan.isNoOp -> local.update { it.copy(plan = null, message = MSG_SAME_ALBUM) }
            // First selection, or the same album with a lost grant: nothing is wiped, so no confirmation.
            !plan.wipesData -> handle(coordinator.confirmChangeAlbum(plan), env, treeUri, applied = null)
            else -> local.update { it.copy(plan = plan, message = note) }
        }
    }

    private suspend fun handle(result: AlbumChangeResult, env: String, treeUri: String, applied: String?) {
        when (result) {
            is AlbumChangeResult.Applied -> local.update { it.copy(plan = null, message = applied) }
            AlbumChangeResult.Unchanged -> local.update { it.copy(plan = null, message = MSG_SAME_ALBUM) }
            AlbumChangeResult.GrantFailed -> local.update { it.copy(plan = null, message = MSG_GRANT_FAILED) }
            AlbumChangeResult.Failed -> local.update { it.copy(plan = null, message = MSG_CHANGE_FAILED) }
            AlbumChangeResult.RequiresConfirmation -> requestChange(env, treeUri)
            AlbumChangeResult.PlanStale -> requestChange(env, treeUri, note = MSG_PLAN_STALE)
        }
    }

    // ---- sync ------------------------------------------------------------------------------------------------

    fun startSync() {
        viewModelScope.launch {
            val env = currentEnvironmentId()
            local.update { it.copy(dismissedSync = null) }
            engine.start(env)
        }
    }

    fun stopSync() {
        viewModelScope.launch { engine.stop(currentEnvironmentId()) }
    }

    /** Hides a finished/stopped/failed banner until the next run ends. */
    fun dismissSyncResult() {
        viewModelScope.launch {
            val state = engine.state(currentEnvironmentId()).value
            if (state.isTerminal()) local.update { it.copy(dismissedSync = state) }
        }
    }

    fun setBatchSize(size: Int) {
        viewModelScope.launch { guarded { tracker.setBatchSize(TrackerSettingsStore.clampBatchSize(size)) } }
    }

    // ---- rejected files --------------------------------------------------------------------------------------

    fun showRejected() = local.update { it.copy(rejectedVisible = true) }

    fun hideRejected() = local.update { it.copy(rejectedVisible = false) }

    fun retryServerErrors() {
        viewModelScope.launch {
            guarded {
                val n = tracker.retryServerErrors(currentEnvironmentId())
                post(if (n > 0) "$n ${if (n == 1) "file" else "files"} will be sent again on the next sync" else "No files with server errors")
            }
        }
    }

    fun retryServerError(item: RejectedItem) {
        viewModelScope.launch {
            guarded {
                val ok = tracker.retryServerError(currentEnvironmentId(), item.albumTreeUri, item.documentId)
                post(if (ok) "${item.displayName} will be sent again on the next sync" else "That file cannot be retried")
            }
        }
    }

    fun dismissMessage() = local.update { it.copy(message = null) }

    // ---- helpers ---------------------------------------------------------------------------------------------

    private suspend fun currentEnvironmentId(): String = selectedEnvironment.first().id

    private fun setBusy(busy: Boolean) = local.update { it.copy(busy = busy) }

    private fun post(message: String) = local.update { it.copy(message = message) }

    /** Data-layer calls do not throw by contract, but a screen must not crash on a disk or database error. */
    private suspend fun guarded(block: suspend () -> Unit) {
        try {
            block()
        } catch (e: CancellationException) {
            throw e
        } catch (_: Exception) {
            post(MSG_UNEXPECTED)
        }
    }

    companion object {
        const val AUTO_REFRESH_MIN_INTERVAL_MS = 30_000L
        const val MSG_SAME_ALBUM = "That album is already selected."
        const val MSG_GRANT_FAILED = "Could not keep access to that folder. Please choose it again."
        const val MSG_CHANGE_FAILED = "Could not save the album. Nothing was changed; try again."
        const val MSG_PLAN_STALE = "The selected album changed in the meantime. Please review the confirmation again."
        const val MSG_ALBUM_CHANGED = "Album changed. The upload list was reset."
        const val MSG_UNEXPECTED = "Something went wrong. Try again."
    }
}
