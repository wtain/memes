package com.memebrowser.app.data.album

import com.memebrowser.app.data.tracker.AlbumSelection
import com.memebrowser.app.data.tracker.TrackedFileEntity
import com.memebrowser.app.data.tracker.TrackerCounts
import com.memebrowser.app.data.tracker.TrackerRepository
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import java.util.concurrent.ConcurrentHashMap
import javax.inject.Inject
import javax.inject.Singleton
import kotlin.coroutines.cancellation.CancellationException

/** Why a scan produced no new data. The tracker was left exactly as it was. */
enum class ScanFailure {
    /** No album stored for the environment. */
    NO_ALBUM,

    /** The provider was still loading or the query failed; try again later. */
    LISTING_INCOMPLETE,

    /** The listing was fine but writing it to the tracker failed. */
    TRACKER_ERROR,

    /** The refresh was cancelled while another caller was waiting on it. */
    CANCELLED,
}

/** Per-environment state of the album scan. In memory only; a new process starts [Idle]. */
sealed interface ScanState {
    data object Idle : ScanState
    data object Scanning : ScanState
    data class Failed(val reason: ScanFailure) : ScanState

    /** The grant is gone: A4 asks the user to pick the album again. The tracker was NOT wiped. */
    data object PermissionLost : ScanState

    /** The last scan finished at [at] (epoch ms). */
    data class Done(val at: Long) : ScanState
}

/**
 * What the confirmation dialog shows before an album change. Built by [AlbumSyncCoordinator.requestChangeAlbum];
 * nothing has been changed yet. [uploaded] / [pending] / [rejected] are the rows that [wipesData] would delete.
 */
data class ChangeAlbumPlan(
    val environmentId: String,
    val newTreeUri: String,
    val newDisplayName: String,
    val currentAlbum: AlbumSelection?,
    val uploaded: Int,
    val pending: Int,
    val rejected: Int,
    /** The new folder is one of the app's own `Pictures/MemesBrowser-*` folders: A4 should warn. */
    val looksLikeAppOwnFolder: Boolean,
    /** [newTreeUri] is the album already stored. */
    val isSameAlbum: Boolean,
    /** Same album, but its persisted grant is gone, so confirming only restores the grant and rescans. */
    val needsRegrant: Boolean,
) {
    /** Confirming deletes this environment's tracker rows (never for a first selection or the same album). */
    val wipesData: Boolean get() = currentAlbum != null && !isSameAlbum

    /** Confirming does nothing at all. */
    val isNoOp: Boolean get() = isSameAlbum && !needsRegrant
}

sealed interface AlbumChangeResult {
    /** The album is stored and a scan ran; [scan] is its outcome (it may be a failure, the album is still set). */
    data class Applied(val scan: ScanState) : AlbumChangeResult

    /** Same album and the grant is intact: nothing was changed. */
    data object Unchanged : AlbumChangeResult

    /** The system refused the persistable grant. Nothing was changed or wiped. */
    data object GrantFailed : AlbumChangeResult

    /** [AlbumSyncCoordinator.selectAlbum] was called while another album is stored; use requestChangeAlbum. */
    data object RequiresConfirmation : AlbumChangeResult
}

/**
 * Album flows for A4, all per environment id (`BackendEnvironment.id`). No Android UI types.
 *
 * Concurrency:
 *  - Per environment, one lock covers a scan's whole body and an album change's wipe, so a scan can never write
 *    rows of the old album after the wipe, and a scan that waits for the lock reads the NEW album.
 *  - [refreshScan] is conflated: a call made while a refresh of the same environment is in flight does not start
 *    another; it waits for and returns the in-flight result.
 *  - Environments never share locks, state or in-flight work.
 *  - [SyncGate.cancelAndAwait] is called BEFORE the wipe (A1 contract); A3 binds the real gate.
 *
 * Automatic refresh (screen open) and manual refresh (button) are both [refreshScan]; A4 wires them.
 */
@Singleton
class AlbumSyncCoordinator(
    private val tracker: TrackerRepository,
    private val scanner: AlbumScanner,
    private val access: AlbumAccess,
    private val gate: SyncGate,
    private val environments: EnvironmentIdSource,
    private val clock: () -> Long,
) {
    @Inject
    constructor(
        tracker: TrackerRepository,
        scanner: AlbumScanner,
        access: AlbumAccess,
        gate: SyncGate,
        environments: EnvironmentIdSource,
    ) : this(tracker, scanner, access, gate, environments, System::currentTimeMillis)

    private val locks = ConcurrentHashMap<String, Mutex>()
    private val states = ConcurrentHashMap<String, MutableStateFlow<ScanState>>()
    private val inFlight = HashMap<String, CompletableDeferred<ScanState>>() // guarded by itself

    private fun lockOf(environmentId: String) = locks.computeIfAbsent(environmentId) { Mutex() }
    private fun stateOf(environmentId: String) = states.computeIfAbsent(environmentId) { MutableStateFlow(ScanState.Idle) }

    // ---- observation -------------------------------------------------------------------------------------

    fun scanState(environmentId: String): StateFlow<ScanState> = stateOf(environmentId).asStateFlow()

    /** X uploaded, Y supported, R rejected, P pending of the environment. */
    fun counts(environmentId: String): Flow<TrackerCounts> = tracker.observeCounts(environmentId)

    /** The stored album, or null until one is selected. */
    fun album(environmentId: String): Flow<AlbumSelection?> = tracker.album(environmentId)

    fun rejectedFiles(environmentId: String): Flow<List<TrackedFileEntity>> = tracker.rejectedFiles(environmentId)

    // ---- album selection ---------------------------------------------------------------------------------

    /**
     * First selection for an environment: persists the grant, stores album and name, then scans. With a DIFFERENT
     * album already stored it does nothing and returns [AlbumChangeResult.RequiresConfirmation]. With the same
     * album it is a no-op, except that a lost grant is restored (and the album rescanned).
     */
    suspend fun selectAlbum(environmentId: String, treeUri: String): AlbumChangeResult {
        val current = tracker.album(environmentId).first()
        return when {
            current == null -> applyFirstSelection(environmentId, treeUri)
            current.treeUri == treeUri -> restoreGrantIfLost(environmentId, treeUri)
            else -> AlbumChangeResult.RequiresConfirmation
        }
    }

    /** Builds the confirmation dialog data. Changes nothing, takes no grant. */
    suspend fun requestChangeAlbum(environmentId: String, newTreeUri: String): ChangeAlbumPlan {
        val current = tracker.album(environmentId).first()
        val counts = tracker.observeCounts(environmentId).first()
        val same = current?.treeUri == newTreeUri
        return ChangeAlbumPlan(
            environmentId = environmentId,
            newTreeUri = newTreeUri,
            newDisplayName = AlbumNames.displayName(newTreeUri),
            currentAlbum = current,
            uploaded = counts.uploaded,
            pending = counts.pending,
            rejected = counts.rejected,
            looksLikeAppOwnFolder = AlbumNames.looksLikeAppOwnFolder(newTreeUri),
            isSameAlbum = same,
            needsRegrant = same && !access.isGranted(newTreeUri),
        )
    }

    /**
     * Applies a plan. The state is re-read, not trusted from the plan. Order for a real change: take the new grant
     * (a refusal changes nothing), `SyncGate.cancelAndAwait`, wipe this environment's rows and store the new album
     * (`TrackerRepository.changeAlbum`), release the old grant unless another environment uses that tree, scan.
     * Same album: no wipe. No album stored (it was cleared meanwhile): behaves as a first selection.
     */
    suspend fun confirmChangeAlbum(plan: ChangeAlbumPlan): AlbumChangeResult {
        val env = plan.environmentId
        val current = tracker.album(env).first()
        return when {
            current == null -> applyFirstSelection(env, plan.newTreeUri)
            current.treeUri == plan.newTreeUri -> restoreGrantIfLost(env, plan.newTreeUri)
            else -> {
                if (!access.persist(plan.newTreeUri)) return AlbumChangeResult.GrantFailed
                gate.cancelAndAwait(env)
                lockOf(env).withLock {
                    tracker.changeAlbum(env, plan.newTreeUri, AlbumNames.displayName(plan.newTreeUri))
                }
                releaseIfUnused(current.treeUri, env)
                AlbumChangeResult.Applied(refreshScan(env))
            }
        }
    }

    private suspend fun applyFirstSelection(env: String, treeUri: String): AlbumChangeResult {
        if (!access.persist(treeUri)) return AlbumChangeResult.GrantFailed
        lockOf(env).withLock { tracker.setAlbum(env, treeUri, AlbumNames.displayName(treeUri)) }
        return AlbumChangeResult.Applied(refreshScan(env))
    }

    private suspend fun restoreGrantIfLost(env: String, treeUri: String): AlbumChangeResult {
        if (access.isGranted(treeUri)) return AlbumChangeResult.Unchanged
        if (!access.persist(treeUri)) return AlbumChangeResult.GrantFailed
        return AlbumChangeResult.Applied(refreshScan(env))
    }

    private suspend fun releaseIfUnused(oldTreeUri: String, exceptEnvironment: String) {
        val usedElsewhere = environments.ids()
            .filter { it != exceptEnvironment }
            .any { tracker.album(it).first()?.treeUri == oldTreeUri }
        if (!usedElsewhere) access.release(oldTreeUri)
    }

    // ---- scan --------------------------------------------------------------------------------------------

    /**
     * Lists the environment's album and reconciles the tracker. Never throws (except cancellation). Outcomes:
     *  - Complete listing: `syncScan(allowEmpty = true)` (an empty album is a real result), state Done.
     *  - Incomplete listing (still loading, query failed): syncScan is NOT called, state Failed(LISTING_INCOMPLETE).
     *  - PermissionLost: syncScan is NOT called, tracker untouched, state PermissionLost.
     *  - No album: Failed(NO_ALBUM).
     * Conflated per environment (see the class KDoc): a concurrent call returns the in-flight result.
     */
    suspend fun refreshScan(environmentId: String): ScanState {
        val mine = CompletableDeferred<ScanState>()
        val running = synchronized(inFlight) {
            inFlight[environmentId] ?: run { inFlight[environmentId] = mine; null }
        }
        if (running != null) return running.await()
        try {
            val result = lockOf(environmentId).withLock { scanLocked(environmentId) }
            mine.complete(result)
            return result
        } finally {
            if (!mine.isCompleted) {
                // Cancelled: waiters get a failure instead of inheriting the cancellation.
                mine.complete(ScanState.Failed(ScanFailure.CANCELLED))
                stateOf(environmentId).compareAndSet(ScanState.Scanning, ScanState.Idle)
            }
            synchronized(inFlight) { inFlight.remove(environmentId, mine) }
        }
    }

    private suspend fun scanLocked(env: String): ScanState {
        val state = stateOf(env)
        val album = tracker.album(env).first()
        if (album == null) {
            return ScanState.Failed(ScanFailure.NO_ALBUM).also { state.value = it }
        }
        state.value = ScanState.Scanning
        val result: ScanState = when (val listing = scanner.scan(album.treeUri)) {
            is AlbumScanResult.Complete -> try {
                tracker.syncScan(env, album.treeUri, listing.files, allowEmpty = true)
                ScanState.Done(clock())
            } catch (e: CancellationException) {
                throw e
            } catch (_: Exception) {
                ScanState.Failed(ScanFailure.TRACKER_ERROR)
            }
            is AlbumScanResult.Incomplete -> ScanState.Failed(ScanFailure.LISTING_INCOMPLETE)
            AlbumScanResult.PermissionLost -> ScanState.PermissionLost
        }
        state.value = result
        return result
    }
}
