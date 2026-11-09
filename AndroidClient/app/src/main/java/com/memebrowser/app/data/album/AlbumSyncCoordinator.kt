package com.memebrowser.app.data.album

import com.memebrowser.app.data.tracker.AlbumSelection
import com.memebrowser.app.data.tracker.TrackedFileEntity
import com.memebrowser.app.data.tracker.TrackerCounts
import com.memebrowser.app.data.tracker.TrackerRepository
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.isActive
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import java.util.concurrent.ConcurrentHashMap
import javax.inject.Inject
import javax.inject.Singleton
import kotlin.coroutines.cancellation.CancellationException

/** Why a scan produced no new data. The tracker was left exactly as it was. */
enum class ScanFailure {
    /** No album stored for the environment. */
    NO_ALBUM,

    /** The provider was still loading, timed out, the album is unavailable or the query failed; try again later. */
    LISTING_INCOMPLETE,

    /** The listing was fine but reading or writing the tracker (database or settings) failed. */
    TRACKER_ERROR,

    /** The refresh was cancelled (its caller left, or an album change aborted it) while another caller was waiting. */
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

    /**
     * The system refused the persistable grant (for example the picker's temporary grant is gone). Nothing was
     * changed or wiped; A4 asks the user to pick again.
     */
    data object GrantFailed : AlbumChangeResult

    /** [AlbumSyncCoordinator.selectAlbum] was called while another album is stored; use requestChangeAlbum. */
    data object RequiresConfirmation : AlbumChangeResult

    /**
     * The stored album is no longer the one the plan was built from (it changed after the dialog opened). Nothing was
     * changed; A4 rebuilds the plan with requestChangeAlbum and asks again.
     */
    data object PlanStale : AlbumChangeResult

    /**
     * Storage failed (tracker database or settings) while changing the album. The new grant was given back; the old
     * album and its grant are kept. If rows were already wiped, the next scan restores them.
     */
    data object Failed : AlbumChangeResult
}

/**
 * Album flows for A4, all per environment id (`BackendEnvironment.id`). No Android UI types.
 *
 * Concurrency:
 *  - Per environment, one lock covers a scan's whole body and an album change's wipe, so a scan can never write
 *    rows of the old album after the wipe, and a scan that waits for the lock reads the NEW album.
 *  - [refreshScan] is conflated: a call made while a refresh of the same environment is in flight does not start
 *    another; it waits for and returns the in-flight result. The entry is removed while the scan still holds the
 *    lock, so a refresh issued after a change can never join the old album's finished result.
 *  - An album change ABORTS the in-flight scan of its environment first (its result is about to be discarded) and
 *    keeps aborting until it owns the lock, so a hung provider can never freeze "change album". The scanner honours
 *    the cancellation (see [SafAlbumScanner]); the waiters of an aborted scan get `Failed(CANCELLED)`.
 *  - Environments never share locks, state or in-flight work.
 *  - [SyncGate.cancelAndAwait] is called BEFORE the wipe (A1 contract); A3 binds the real gate.
 *  - Cancellation safety of a change: the new grant is taken first and given back if the gate step is cancelled or
 *    fails. After the gate the commit (wipe + store + give back the old grant) runs in `NonCancellable`, so a
 *    cancelled caller (screen left) can neither leak the new grant nor the old one. Only the final rescan is
 *    cancellable (the album is already changed; the next refresh scans it).
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

    private class InFlight(val result: CompletableDeferred<ScanState>, val job: Job)

    private enum class Commit { APPLIED, STALE, FAILED }

    private val locks = ConcurrentHashMap<String, Mutex>()
    private val states = ConcurrentHashMap<String, MutableStateFlow<ScanState>>()
    private val inFlight = HashMap<String, InFlight>() // guarded by itself

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
    suspend fun selectAlbum(environmentId: String, treeUri: String): AlbumChangeResult = guardChange {
        val current = tracker.album(environmentId).first()
        when {
            current == null -> applyFirstSelection(environmentId, treeUri, stale = AlbumChangeResult.RequiresConfirmation)
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
            // A scan that reports PermissionLost counts as a lost grant even if the grant is still listed (the provider
            // may throw SecurityException anyway): re-picking the same folder then re-takes the grant and rescans.
            needsRegrant = same && (!access.isGranted(newTreeUri) || stateOf(environmentId).value == ScanState.PermissionLost),
        )
    }

    /**
     * Applies a plan. The stored album must still be the one the plan was built from, otherwise nothing happens
     * and the result is [AlbumChangeResult.PlanStale]. Order for a real change: take the new grant (a refusal
     * changes nothing), `SyncGate.cancelAndAwait`, abort the in-flight scan, wipe this environment's rows and
     * store the new album (`TrackerRepository.changeAlbum`, album re-checked under the lock), release the old
     * grant unless another environment uses that tree, scan. Same album: no wipe.
     */
    suspend fun confirmChangeAlbum(plan: ChangeAlbumPlan): AlbumChangeResult = guardChange {
        val env = plan.environmentId
        val current = tracker.album(env).first()
        when {
            current?.treeUri != plan.currentAlbum?.treeUri -> AlbumChangeResult.PlanStale
            current == null -> applyFirstSelection(env, plan.newTreeUri, stale = AlbumChangeResult.PlanStale)
            current.treeUri == plan.newTreeUri -> restoreGrantIfLost(env, plan.newTreeUri)
            else -> changeAlbumNow(env, current.treeUri, plan.newTreeUri)
        }
    }

    private suspend fun changeAlbumNow(env: String, oldTree: String, newTree: String): AlbumChangeResult {
        if (!access.persist(newTree)) return AlbumChangeResult.GrantFailed
        try {
            gate.cancelAndAwait(env)
        } catch (e: Throwable) {
            // Not past the point of no return: give the new grant back (unless somebody else stores that tree).
            withContext(NonCancellable) { releaseIfUnused(newTree, env) }
            if (e is CancellationException) throw e
            return AlbumChangeResult.Failed
        }
        return finish(env, commit(env, expectedOld = oldTree, newTree = newTree), AlbumChangeResult.PlanStale)
    }

    private suspend fun applyFirstSelection(env: String, treeUri: String, stale: AlbumChangeResult): AlbumChangeResult {
        if (!access.persist(treeUri)) return AlbumChangeResult.GrantFailed
        return finish(env, commit(env, expectedOld = null, newTree = treeUri), stale)
    }

    private suspend fun finish(env: String, outcome: Commit, stale: AlbumChangeResult): AlbumChangeResult = when (outcome) {
        Commit.APPLIED -> AlbumChangeResult.Applied(refreshScan(env)) // cancellable on purpose: the album is already set
        Commit.STALE -> stale
        Commit.FAILED -> AlbumChangeResult.Failed
    }

    /**
     * The point of no return, in `NonCancellable`: owns the environment lock (aborting scans until it does), checks
     * the stored album is still [expectedOld] (null = none), writes (wipe + store, or store), resets the scan
     * state, then gives back the OLD grant on success or the NEW grant on stale/failure.
     */
    private suspend fun commit(env: String, expectedOld: String?, newTree: String): Commit = withContext(NonCancellable) {
        val lock = lockOf(env)
        while (!lock.tryLock()) {
            abortInFlight(env)
            delay(LOCK_POLL_MILLIS)
        }
        var outcome: Commit
        try {
            val now = tracker.album(env).first()
            if (now?.treeUri != expectedOld) {
                outcome = Commit.STALE
            } else {
                val name = AlbumNames.displayName(newTree)
                if (expectedOld == null) tracker.setAlbum(env, newTree, name) else tracker.changeAlbum(env, newTree, name)
                stateOf(env).value = ScanState.Idle
                outcome = Commit.APPLIED
            }
        } catch (_: Exception) {
            outcome = Commit.FAILED
        } finally {
            lock.unlock()
        }
        if (outcome == Commit.APPLIED) {
            if (expectedOld != null) releaseIfUnused(expectedOld, env)
        } else {
            releaseIfUnused(newTree, env)
        }
        outcome
    }

    private suspend fun restoreGrantIfLost(env: String, treeUri: String): AlbumChangeResult {
        if (access.isGranted(treeUri) && stateOf(env).value != ScanState.PermissionLost) return AlbumChangeResult.Unchanged
        if (!access.persist(treeUri)) return AlbumChangeResult.GrantFailed
        return AlbumChangeResult.Applied(refreshScan(env))
    }

    /**
     * Gives [treeUri] back unless some environment (this one included) stores it. If the environments cannot be
     * read the grant is kept: a leaked grant is bounded, a wrongly released one breaks another environment.
     */
    private suspend fun releaseIfUnused(treeUri: String, env: String) {
        try {
            val usedElsewhere = (environments.ids() + env).distinct()
                .any { tracker.album(it).first()?.treeUri == treeUri }
            if (!usedElsewhere) access.release(treeUri)
        } catch (_: Exception) {
            // Keep the grant.
        }
    }

    private inline fun guardChange(block: () -> AlbumChangeResult): AlbumChangeResult = try {
        block()
    } catch (e: CancellationException) {
        throw e
    } catch (_: Exception) {
        AlbumChangeResult.Failed
    }

    // ---- scan --------------------------------------------------------------------------------------------

    /**
     * Lists the environment's album and reconciles the tracker. Never throws (except cancellation of the caller).
     * Outcomes:
     *  - Complete listing: `syncScan(allowEmpty = true)` (an empty album is a real result), state Done.
     *  - Incomplete listing (still loading, query failed or timed out, album root gone): syncScan is NOT called,
     *    state Failed(LISTING_INCOMPLETE).
     *  - PermissionLost: syncScan is NOT called, tracker untouched, state PermissionLost.
     *  - No album: Failed(NO_ALBUM). Tracker or settings failure: Failed(TRACKER_ERROR).
     *  - Aborted by an album change: Failed(CANCELLED), state Idle.
     * Conflated per environment (see the class KDoc): a concurrent call returns the in-flight result.
     */
    @OptIn(ExperimentalCoroutinesApi::class)
    suspend fun refreshScan(environmentId: String): ScanState {
        val mine = CompletableDeferred<ScanState>()
        val scanJob = Job(currentCoroutineContext()[Job]) // cancelled by the caller or by abortInFlight
        val entry = InFlight(mine, scanJob)
        val running = synchronized(inFlight) {
            inFlight[environmentId] ?: run { inFlight[environmentId] = entry; null }
        }
        if (running != null) {
            scanJob.cancel()
            return running.result.await()
        }
        try {
            return withContext(scanJob) {
                lockOf(environmentId).withLock {
                    val result = scanGuarded(environmentId)
                    mine.complete(result)
                    // Still under the lock: nobody can join this (soon stale) result after a change took over.
                    synchronized(inFlight) { inFlight.remove(environmentId, entry) }
                    result
                }
            }
        } catch (e: CancellationException) {
            if (!currentCoroutineContext().isActive) throw e // the caller itself was cancelled
            return if (mine.isCompleted) mine.getCompleted() else ScanState.Failed(ScanFailure.CANCELLED)
        } finally {
            val wasCancelled = !mine.isCompleted
            if (wasCancelled) mine.complete(ScanState.Failed(ScanFailure.CANCELLED)) // waiters do not inherit the cancellation
            val other = synchronized(inFlight) {
                inFlight.remove(environmentId, entry)
                inFlight[environmentId]
            }
            if (wasCancelled && other == null) stateOf(environmentId).compareAndSet(ScanState.Scanning, ScanState.Idle)
            scanJob.cancel()
        }
    }

    /** Detaches and cancels the environment's in-flight scan, if any. Its waiters get Failed(CANCELLED). */
    private fun abortInFlight(env: String) {
        val entry = synchronized(inFlight) { inFlight.remove(env) }
        entry?.job?.cancel(CancellationException("album change"))
    }

    private suspend fun scanGuarded(env: String): ScanState = try {
        scanLocked(env)
    } catch (e: CancellationException) {
        throw e
    } catch (_: Exception) {
        ScanState.Failed(ScanFailure.TRACKER_ERROR).also { stateOf(env).value = it }
    }

    private suspend fun scanLocked(env: String): ScanState {
        val state = stateOf(env)
        val album = tracker.album(env).first()
        if (album == null) {
            return ScanState.Failed(ScanFailure.NO_ALBUM).also { state.value = it }
        }
        state.value = ScanState.Scanning
        val listing = try {
            scanner.scan(album.treeUri)
        } catch (e: CancellationException) {
            throw e
        } catch (_: Exception) {
            AlbumScanResult.Incomplete("scanner failed") // contract violation, treated as a failed listing
        }
        val result: ScanState = when (listing) {
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

    private companion object {
        const val LOCK_POLL_MILLIS = 10L
    }
}
