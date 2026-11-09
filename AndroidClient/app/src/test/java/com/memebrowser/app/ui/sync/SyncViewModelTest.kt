package com.memebrowser.app.ui.sync

import com.memebrowser.app.data.album.AlbumChangeResult
import com.memebrowser.app.data.album.AlbumSyncCoordinator
import com.memebrowser.app.data.album.ChangeAlbumPlan
import com.memebrowser.app.data.album.ScanFailure
import com.memebrowser.app.data.album.ScanState
import com.memebrowser.app.data.model.BackendEnvironment
import com.memebrowser.app.data.repository.EnvironmentRepository
import com.memebrowser.app.data.repository.EnvironmentWithSelection
import com.memebrowser.app.data.sync.SyncEngine
import com.memebrowser.app.data.sync.SyncState
import com.memebrowser.app.data.sync.SyncSummary
import com.memebrowser.app.data.tracker.AlbumSelection
import com.memebrowser.app.data.tracker.RejectionReasons
import com.memebrowser.app.data.tracker.TrackedFileEntity
import com.memebrowser.app.data.tracker.TrackedFileState
import com.memebrowser.app.data.tracker.TrackerCounts
import com.memebrowser.app.data.tracker.TrackerRepository
import com.memebrowser.app.util.MainDispatcherRule
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.just
import io.mockk.mockk
import io.mockk.runs
import io.mockk.verify
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class SyncViewModelTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    private val envNames = mapOf("env-a" to "General", "env-b" to "Metal")
    private fun envList(selected: String) =
        envNames.map { (id, name) -> EnvironmentWithSelection(BackendEnvironment(id, name, "http://$id"), id == selected) }

    /** ONE emission carries id and name together, like the real repository. */
    private val envs = MutableStateFlow(envList("env-a"))
    private fun selectEnv(id: String) {
        envs.value = envList(id)
    }

    private val batch = MutableStateFlow(50)
    private var now = 1_000_000L

    private val albums = HashMap<String, MutableStateFlow<AlbumSelection?>>()
    private val counts = HashMap<String, MutableStateFlow<TrackerCounts>>()
    private val scans = HashMap<String, MutableStateFlow<ScanState>>()
    private val syncs = HashMap<String, MutableStateFlow<SyncState>>()
    private val rejected = HashMap<String, MutableStateFlow<List<TrackedFileEntity>>>()

    private fun <T> HashMap<String, MutableStateFlow<T>>.of(env: String, initial: T) = getOrPut(env) { MutableStateFlow(initial) }
    private fun album(env: String) = albums.of(env, null)
    private fun counts(env: String) = counts.of(env, TrackerCounts.EMPTY)
    private fun scan(env: String) = scans.of(env, ScanState.Idle)
    private fun sync(env: String) = syncs.of(env, SyncState.Idle)
    private fun rejected(env: String) = rejected.of(env, emptyList())

    private val treeA = "content://x/tree/primary%3ADCIM%2FCamera"
    private val treeB = "content://x/tree/primary%3APictures%2FMemes"
    private val selA = AlbumSelection(treeA, "Camera")

    private val environments = mockk<EnvironmentRepository>().also {
        every { it.environmentsWithSelection } returns envs
    }
    private val rejectedOverride = HashMap<String, Flow<List<TrackedFileEntity>>>()
    private val coordinator = mockk<AlbumSyncCoordinator>().also {
        every { it.album(any()) } answers { album(firstArg()) }
        every { it.counts(any()) } answers { counts(firstArg()) }
        every { it.scanState(any()) } answers { scan(firstArg()) }
        every { it.rejectedFiles(any()) } answers { rejectedOverride[firstArg()] ?: rejected(firstArg()) }
        coEvery { it.refreshScan(any()) } returns ScanState.Done(1)
    }
    private val engine = mockk<SyncEngine>().also {
        every { it.state(any()) } answers { sync(firstArg()) }
        every { it.start(any()) } returns true
        every { it.stop(any()) } just runs
    }
    private val tracker = mockk<TrackerRepository>().also {
        every { it.batchSize() } returns batch
        coEvery { it.setBatchSize(any()) } answers { batch.value = firstArg() }
    }

    private fun vm(mapping: CoroutineDispatcher = mainDispatcherRule.dispatcher) =
        SyncViewModel(environments, coordinator, engine, tracker, { now }, mapping)

    /** Subscribes to the state for the duration of the test. */
    private fun TestScope.started(vm: SyncViewModel): SyncViewModel {
        backgroundScope.launch(UnconfinedTestDispatcher(testScheduler)) { vm.uiState.collect { } }
        return vm
    }

    private fun plan(
        env: String = "env-a",
        tree: String = treeB,
        current: AlbumSelection? = selA,
        same: Boolean = false,
        regrant: Boolean = false,
    ) = ChangeAlbumPlan(env, tree, "Memes", current, uploaded = 5, pending = 3, rejected = 1, looksLikeAppOwnFolder = false, isSameAlbum = same, needsRegrant = regrant)

    private fun entity(id: String, reason: String?) = TrackedFileEntity(
        "env-a", treeA, id, "$id.jpg", "image/jpeg", 1, 1, TrackedFileState.REJECTED, reason, null, 1, true, 1,
    )

    // ---- binding --------------------------------------------------------------------------------------------

    @Test
    fun `state shows the selected environment, album, counts and batch size`() = runTest {
        album("env-a").value = selA
        counts("env-a").value = TrackerCounts(uploaded = 4, supported = 10, rejected = 1, pending = 5)
        batch.value = 7
        val vm = started(vm())
        val s = vm.uiState.value
        assertTrue(s.loaded)
        assertEquals(EnvironmentInfo("env-a", "General"), s.environment)
        assertEquals(selA, s.album)
        assertEquals(10, s.counts.supported)
        assertEquals(7, s.batchSize)
        assertTrue(s.controls.canSync)
    }

    @Test
    fun `switching environment rebinds everything and never mixes environments`() = runTest {
        album("env-a").value = selA
        counts("env-a").value = TrackerCounts(1, 2, 0, 1)
        sync("env-a").value = SyncState.Running(1, 1, 1, SyncSummary())
        counts("env-b").value = TrackerCounts.EMPTY
        val vm = started(vm())
        assertTrue(vm.uiState.value.sync is SyncState.Running)

        selectEnv("env-b")
        val s = vm.uiState.value
        assertEquals(EnvironmentInfo("env-b", "Metal"), s.environment)
        assertNull(s.album)
        assertEquals(TrackerCounts.EMPTY, s.counts)
        assertEquals(SyncState.Idle, s.sync)

        counts("env-a").value = TrackerCounts(9, 9, 0, 0) // a change in the old environment is not seen
        assertEquals(TrackerCounts.EMPTY, vm.uiState.value.counts)
    }

    @Test
    fun `a change plan of another environment is not shown`() = runTest {
        album("env-a").value = selA
        coEvery { coordinator.requestChangeAlbum("env-a", treeB) } returns plan()
        val vm = started(vm())
        vm.onAlbumPicked(treeB)
        assertEquals("env-a", vm.uiState.value.changePlan?.environmentId)
        selectEnv("env-b")
        assertNull(vm.uiState.value.changePlan)
    }

    @Test
    fun `a switch never shows the new id with the old name or the reverse`() = runTest {
        val seen = mutableListOf<EnvironmentInfo?>()
        val vm = vm()
        backgroundScope.launch(UnconfinedTestDispatcher(testScheduler)) { vm.uiState.collect { seen += it.environment } }
        selectEnv("env-b")
        selectEnv("env-a")
        val valid = envNames.map { (id, name) -> EnvironmentInfo(id, name) }.toSet()
        assertTrue(seen.filterNotNull().isNotEmpty())
        seen.filterNotNull().forEach { assertTrue("$it", it in valid) }
    }

    @Test
    fun `no selected environment gives a loaded state without environment instead of an endless spinner`() = runTest {
        envs.value = envList("none")
        val vm = started(vm())
        assertTrue(vm.uiState.value.loaded)
        assertNull(vm.uiState.value.environment)
        vm.startSync() // acts on nothing
        verify(exactly = 0) { engine.start(any()) }
    }

    @Test
    fun `start and stop act on the environment that was switched to, never on the old one`() = runTest {
        album("env-a").value = selA
        val vm = started(vm())
        selectEnv("env-b")
        vm.startSync()
        vm.stopSync()
        verify(exactly = 1) { engine.start("env-b") }
        verify(exactly = 1) { engine.stop("env-b") }
        verify(exactly = 0) { engine.start("env-a") }
        verify(exactly = 0) { engine.stop("env-a") }
    }

    @Test
    fun `message, busy, dismissed result and the open rejected list do not leak into another environment`() = runTest {
        album("env-a").value = selA
        sync("env-a").value = SyncState.Finished(SyncSummary(uploaded = 1), at = 5)
        sync("env-b").value = SyncState.Finished(SyncSummary(uploaded = 1), at = 5) // identical to env-a's
        coEvery { tracker.retryServerErrors("env-a", any()) } returns 0
        val vm = started(vm())
        vm.retryServerErrors() // a message in env-a
        vm.showRejected()
        vm.dismissSyncResult()
        assertEquals("No files with server errors", vm.uiState.value.message)
        assertTrue(vm.uiState.value.rejectedVisible)
        assertEquals(SyncState.Idle, vm.uiState.value.sync)

        selectEnv("env-b")
        val b = vm.uiState.value
        assertNull(b.message)
        assertFalse(b.rejectedVisible)
        assertTrue(b.sync is SyncState.Finished) // env-a's dismissal does not hide env-b's banner

        selectEnv("env-a") // back: env-a's own state is still its own
        assertTrue(vm.uiState.value.rejectedVisible)
    }

    @Test
    fun `a busy album pick in one environment does not disable the other`() = runTest {
        val gate = CompletableDeferred<AlbumChangeResult>()
        coEvery { coordinator.selectAlbum("env-a", treeA) } coAnswers { gate.await() }
        val vm = started(vm())
        vm.onAlbumPicked(treeA)
        assertTrue(vm.uiState.value.busy)
        selectEnv("env-b")
        assertFalse(vm.uiState.value.busy)
        gate.complete(AlbumChangeResult.Unchanged)
        selectEnv("env-a")
        assertFalse(vm.uiState.value.busy)
    }

    // ---- refresh ------------------------------------------------------------------------------------------------

    @Test
    fun `opening the screen refreshes once, double starts and rotation are throttled`() = runTest {
        album("env-a").value = selA
        val vm = started(vm())
        vm.onScreenStarted()
        vm.onScreenStarted()
        now += 5_000
        vm.onScreenStarted()
        coVerify(exactly = 1) { coordinator.refreshScan("env-a") }
        now += SyncViewModel.AUTO_REFRESH_MIN_INTERVAL_MS
        vm.onScreenStarted()
        coVerify(exactly = 2) { coordinator.refreshScan("env-a") }
    }

    @Test
    fun `no automatic refresh without an album, and the skipped call does not use up the slot`() = runTest {
        val vm = started(vm())
        vm.onScreenStarted() // env-a has no album
        coVerify(exactly = 0) { coordinator.refreshScan(any()) }
        album("env-a").value = selA
        vm.onScreenStarted() // same instant: the earlier skip left the slot free
        coVerify(exactly = 1) { coordinator.refreshScan("env-a") }
    }

    @Test
    fun `no automatic refresh during a scan, and that skip does not use up the slot`() = runTest {
        album("env-a").value = selA
        scan("env-a").value = ScanState.Scanning
        val vm = started(vm())
        vm.onScreenStarted()
        coVerify(exactly = 0) { coordinator.refreshScan(any()) }
        scan("env-a").value = ScanState.Idle
        vm.onScreenStarted()
        coVerify(exactly = 1) { coordinator.refreshScan("env-a") }
    }

    @Test
    fun `a failed refresh does not use up the slot but a good one does`() = runTest {
        album("env-a").value = selA
        coEvery { coordinator.refreshScan("env-a") } returns ScanState.Failed(ScanFailure.LISTING_INCOMPLETE)
        val vm = started(vm())
        vm.onScreenStarted()
        vm.onScreenStarted() // retried at once: the first one failed
        coVerify(exactly = 2) { coordinator.refreshScan("env-a") }
        coEvery { coordinator.refreshScan("env-a") } returns ScanState.Done(1)
        vm.onScreenStarted()
        vm.onScreenStarted() // throttled now
        coVerify(exactly = 3) { coordinator.refreshScan("env-a") }
    }

    @Test
    fun `each environment refreshes on its own slot`() = runTest {
        album("env-a").value = selA
        album("env-b").value = selA
        val vm = started(vm())
        vm.onScreenStarted()
        selectEnv("env-b")
        vm.onScreenStarted()
        coVerify(exactly = 1) { coordinator.refreshScan("env-a") }
        coVerify(exactly = 1) { coordinator.refreshScan("env-b") }
    }

    @Test
    fun `manual refresh runs unless a scan is already running`() = runTest {
        album("env-a").value = selA
        val vm = started(vm())
        vm.refresh()
        coVerify(exactly = 1) { coordinator.refreshScan("env-a") }
        scan("env-a").value = ScanState.Scanning
        vm.refresh()
        coVerify(exactly = 1) { coordinator.refreshScan("env-a") }
    }

    // ---- first selection -----------------------------------------------------------------------------------------

    @Test
    fun `first selection calls selectAlbum and shows no message when applied`() = runTest {
        coEvery { coordinator.selectAlbum("env-a", treeA) } returns AlbumChangeResult.Applied(ScanState.Done(1))
        val vm = started(vm())
        vm.onAlbumPicked(treeA)
        coVerify { coordinator.selectAlbum("env-a", treeA) }
        assertNull(vm.uiState.value.message)
        assertFalse(vm.uiState.value.busy)
    }

    @Test
    fun `first selection results map to messages`() = runTest {
        val vm = started(vm())
        listOf(
            AlbumChangeResult.GrantFailed to SyncViewModel.MSG_GRANT_FAILED,
            AlbumChangeResult.Failed to SyncViewModel.MSG_CHANGE_FAILED,
            AlbumChangeResult.Unchanged to SyncViewModel.MSG_SAME_ALBUM,
        ).forEach { (result, message) ->
            coEvery { coordinator.selectAlbum("env-a", treeA) } returns result
            vm.onAlbumPicked(treeA)
            assertEquals(message, vm.uiState.value.message)
            vm.dismissMessage()
            assertNull(vm.uiState.value.message)
        }
    }

    @Test
    fun `selectAlbum answering RequiresConfirmation goes through the change flow`() = runTest {
        coEvery { coordinator.selectAlbum("env-a", treeB) } answers {
            album("env-a").value = selA // an album appeared meanwhile, which is why confirmation is required
            AlbumChangeResult.RequiresConfirmation
        }
        coEvery { coordinator.requestChangeAlbum("env-a", treeB) } returns plan()
        val vm = started(vm())
        vm.onAlbumPicked(treeB) // the ViewModel believes there is no album yet
        assertEquals(treeB, vm.uiState.value.changePlan?.newTreeUri)
    }

    // ---- change album --------------------------------------------------------------------------------------------

    @Test
    fun `changing the album shows the plan and confirming applies it`() = runTest {
        album("env-a").value = selA
        val p = plan()
        coEvery { coordinator.requestChangeAlbum("env-a", treeB) } returns p
        coEvery { coordinator.confirmChangeAlbum(p) } returns AlbumChangeResult.Applied(ScanState.Done(1))
        val vm = started(vm())

        vm.onAlbumPicked(treeB)
        assertEquals(p, vm.uiState.value.changePlan)
        coVerify(exactly = 0) { coordinator.confirmChangeAlbum(any()) } // nothing is wiped before the user confirms
        coVerify(exactly = 0) { coordinator.selectAlbum(any(), any()) }

        vm.confirmChangeAlbum()
        coVerify(exactly = 1) { coordinator.confirmChangeAlbum(p) }
        assertNull(vm.uiState.value.changePlan)
        assertEquals(SyncViewModel.MSG_ALBUM_CHANGED, vm.uiState.value.message)
    }

    @Test
    fun `cancelling the dialog changes nothing`() = runTest {
        album("env-a").value = selA
        coEvery { coordinator.requestChangeAlbum("env-a", treeB) } returns plan()
        val vm = started(vm())
        vm.onAlbumPicked(treeB)
        vm.dismissChangeAlbum()
        assertNull(vm.uiState.value.changePlan)
        coVerify(exactly = 0) { coordinator.confirmChangeAlbum(any()) }
    }

    @Test
    fun `the plan lives in the ViewModel, so a new screen subscription still sees it`() = runTest {
        album("env-a").value = selA
        coEvery { coordinator.requestChangeAlbum("env-a", treeB) } returns plan()
        val vm = vm()
        val first = backgroundScope.launch(UnconfinedTestDispatcher(testScheduler)) { vm.uiState.collect { } }
        vm.onAlbumPicked(treeB)
        first.cancel() // the activity went away (rotation)
        backgroundScope.launch(UnconfinedTestDispatcher(testScheduler)) { vm.uiState.collect { } }
        assertEquals(treeB, vm.uiState.value.changePlan?.newTreeUri)
        coVerify(exactly = 1) { coordinator.requestChangeAlbum(any(), any()) }
    }

    @Test
    fun `a stale plan is rebuilt and the user is asked again`() = runTest {
        album("env-a").value = selA
        val p1 = plan()
        val p2 = plan(current = AlbumSelection("content://x/tree/other", "Other"))
        coEvery { coordinator.requestChangeAlbum("env-a", treeB) } returnsMany listOf(p1, p2)
        coEvery { coordinator.confirmChangeAlbum(p1) } answers {
            album("env-a").value = AlbumSelection("content://x/tree/other", "Other") // the stored album changed meanwhile
            AlbumChangeResult.PlanStale
        }
        val vm = started(vm())
        vm.onAlbumPicked(treeB)
        vm.confirmChangeAlbum()
        assertEquals(p2, vm.uiState.value.changePlan)
        assertEquals(SyncViewModel.MSG_PLAN_STALE, vm.uiState.value.message)
    }

    @Test
    fun `grant failure and storage failure close the dialog with a message`() = runTest {
        album("env-a").value = selA
        val p = plan()
        coEvery { coordinator.requestChangeAlbum("env-a", treeB) } returns p
        val vm = started(vm())

        coEvery { coordinator.confirmChangeAlbum(p) } returns AlbumChangeResult.GrantFailed
        vm.onAlbumPicked(treeB)
        vm.confirmChangeAlbum()
        assertNull(vm.uiState.value.changePlan)
        assertEquals(SyncViewModel.MSG_GRANT_FAILED, vm.uiState.value.message)

        coEvery { coordinator.confirmChangeAlbum(p) } returns AlbumChangeResult.Failed
        vm.onAlbumPicked(treeB)
        vm.confirmChangeAlbum()
        assertNull(vm.uiState.value.changePlan)
        assertEquals(SyncViewModel.MSG_CHANGE_FAILED, vm.uiState.value.message)
    }

    @Test
    fun `picking the same album is a message, not a dialog`() = runTest {
        album("env-a").value = selA
        coEvery { coordinator.requestChangeAlbum("env-a", treeA) } returns plan(tree = treeA, same = true)
        val vm = started(vm())
        vm.onAlbumPicked(treeA)
        assertNull(vm.uiState.value.changePlan)
        assertEquals(SyncViewModel.MSG_SAME_ALBUM, vm.uiState.value.message)
        coVerify(exactly = 0) { coordinator.confirmChangeAlbum(any()) }
    }

    @Test
    fun `choosing the same album again after a lost grant restores it without a dialog`() = runTest {
        album("env-a").value = selA
        scan("env-a").value = ScanState.PermissionLost
        val p = plan(tree = treeA, same = true, regrant = true)
        coEvery { coordinator.requestChangeAlbum("env-a", treeA) } returns p
        coEvery { coordinator.confirmChangeAlbum(p) } returns AlbumChangeResult.Applied(ScanState.Done(1))
        val vm = started(vm())
        assertFalse(vm.uiState.value.controls.canSync) // a lost grant blocks Sync now
        vm.onAlbumPicked(treeA)
        coVerify(exactly = 1) { coordinator.confirmChangeAlbum(p) }
        assertNull(vm.uiState.value.changePlan)
    }

    // ---- sync ---------------------------------------------------------------------------------------------------

    @Test
    fun `sync now starts the engine for the selected environment and stop stops it`() = runTest {
        album("env-a").value = selA
        val vm = started(vm())
        vm.startSync()
        verify { engine.start("env-a") }
        vm.stopSync()
        verify { engine.stop("env-a") }
    }

    @Test
    fun `a terminal result can be dismissed and a new run shows again`() = runTest {
        album("env-a").value = selA
        val done = SyncState.Finished(SyncSummary(uploaded = 3), at = 5)
        sync("env-a").value = done
        val vm = started(vm())
        assertEquals(done, vm.uiState.value.sync)
        vm.dismissSyncResult()
        assertEquals(SyncState.Idle, vm.uiState.value.sync)

        vm.startSync()
        sync("env-a").value = SyncState.Running(1, 1, 5, SyncSummary())
        assertTrue(vm.uiState.value.sync is SyncState.Running)
        sync("env-a").value = done // the same terminal value again after a new start must show
        assertEquals(done, vm.uiState.value.sync)
    }

    @Test
    fun `a running state cannot be dismissed`() = runTest {
        sync("env-a").value = SyncState.Running(1, 1, 5, SyncSummary())
        val vm = started(vm())
        vm.dismissSyncResult()
        assertTrue(vm.uiState.value.sync is SyncState.Running)
    }

    @Test
    fun `batch size is clamped to 1 to 50 and stored`() = runTest {
        val vm = started(vm())
        vm.setBatchSize(0)
        assertEquals(1, vm.uiState.value.batchSize)
        vm.setBatchSize(500)
        assertEquals(50, vm.uiState.value.batchSize)
        vm.setBatchSize(12)
        assertEquals(12, vm.uiState.value.batchSize)
    }

    // ---- rejected list --------------------------------------------------------------------------------------------

    @Test
    fun `the rejected list is only loaded while it is shown`() = runTest {
        album("env-a").value = selA
        rejected("env-a").value = listOf(entity("a", RejectionReasons.SERVER_ERROR), entity("b", "unsupported_type"))
        val vm = started(vm())
        assertTrue(vm.uiState.value.rejected.isEmpty())
        vm.showRejected()
        val items = vm.uiState.value.rejected
        assertEquals(2, items.size)
        assertTrue(items[0].canRetry)
        assertFalse(items[1].canRetry)
        assertEquals("Not a supported image type", items[1].reasonText)
        assertEquals(1, vm.uiState.value.retryableCount)
        vm.hideRejected()
        assertTrue(vm.uiState.value.rejected.isEmpty())
    }

    @Test
    fun `retry all and retry one go to the tracker for the selected environment`() = runTest {
        album("env-a").value = selA
        coEvery { tracker.retryServerErrors("env-a", any()) } returns 2
        coEvery { tracker.retryServerError("env-a", treeA, "a", any()) } returns true
        val vm = started(vm())
        vm.retryServerErrors()
        assertEquals("2 files will be sent again on the next sync", vm.uiState.value.message)
        vm.dismissMessage()
        vm.retryServerError(RejectedItem(treeA, "a", "a.jpg", "x", true))
        coVerify { tracker.retryServerError("env-a", treeA, "a", any()) }
        assertEquals("a.jpg will be sent again on the next sync", vm.uiState.value.message)
    }

    @Test
    fun `a data layer exception becomes a message instead of a crash`() = runTest {
        coEvery { tracker.retryServerErrors(any(), any()) } throws java.io.IOException("disk")
        val vm = started(vm())
        vm.retryServerErrors()
        assertEquals(SyncViewModel.MSG_UNEXPECTED, vm.uiState.value.message)
    }

    @Test
    fun `retry answers for zero rows and for a file that cannot be retried`() = runTest {
        coEvery { tracker.retryServerErrors("env-a", any()) } returns 0
        coEvery { tracker.retryServerError("env-a", treeA, "a", any()) } returns false
        coEvery { tracker.retryServerErrors("env-a", any()) } returnsMany listOf(0, 1)
        val vm = started(vm())
        vm.retryServerErrors()
        assertEquals("No files with server errors", vm.uiState.value.message)
        vm.retryServerErrors()
        assertEquals("1 file will be sent again on the next sync", vm.uiState.value.message)
        vm.retryServerError(RejectedItem(treeA, "a", "a.jpg", "x", true))
        assertEquals("That file cannot be retried", vm.uiState.value.message)
    }

    @Test
    fun `an unexpected exception shows a generic message without the exception text`() = runTest {
        coEvery { tracker.retryServerErrors(any(), any()) } throws java.io.IOException("secret path /sdcard/x")
        val vm = started(vm())
        vm.retryServerErrors()
        assertEquals(SyncViewModel.MSG_UNEXPECTED, vm.uiState.value.message)
        assertFalse(vm.uiState.value.message!!.contains("secret"))
    }

    // ---- change album: dialog lifetime, bounded rebuilds ----------------------------------------------------------

    @Test
    fun `the dialog closes as soon as the album is committed, while the rescan is still running`() = runTest {
        album("env-a").value = selA
        val p = plan()
        val rescan = CompletableDeferred<AlbumChangeResult>()
        coEvery { coordinator.requestChangeAlbum("env-a", treeB) } returns p
        coEvery { coordinator.confirmChangeAlbum(p) } coAnswers { rescan.await() }
        val vm = started(vm())
        vm.onAlbumPicked(treeB)
        assertEquals(p, vm.uiState.value.changePlan)
        vm.confirmChangeAlbum()
        assertEquals(p, vm.uiState.value.changePlan) // committed? not yet

        album("env-a").value = AlbumSelection(treeB, "Memes") // wipe + store committed; the coordinator now rescans
        scan("env-a").value = ScanState.Scanning
        val s = vm.uiState.value
        assertNull(s.changePlan) // dialog gone, no hung dialog with dead buttons
        assertEquals(ScanState.Scanning, s.scan) // progress is the main screen's scan state
        assertEquals(treeB, s.album?.treeUri)

        rescan.complete(AlbumChangeResult.Applied(ScanState.Done(1)))
        assertFalse(vm.uiState.value.busy)
        assertEquals(SyncViewModel.MSG_ALBUM_CHANGED, vm.uiState.value.message)
    }

    @Test
    fun `a plan that is stale twice stops with a message instead of looping`() = runTest {
        album("env-a").value = selA
        val wiping = plan()
        val nonWiping = plan(current = selA, tree = treeA, same = true, regrant = true) // rebuilt plan that auto-confirms
        coEvery { coordinator.requestChangeAlbum("env-a", treeB) } returnsMany listOf(wiping, nonWiping, nonWiping, nonWiping)
        coEvery { coordinator.confirmChangeAlbum(any()) } returns AlbumChangeResult.PlanStale
        val vm = started(vm())
        vm.onAlbumPicked(treeB)
        vm.confirmChangeAlbum()
        assertNull(vm.uiState.value.changePlan)
        assertEquals(SyncViewModel.MSG_PLAN_KEEPS_CHANGING, vm.uiState.value.message)
        coVerify(exactly = 2) { coordinator.confirmChangeAlbum(any()) } // the user's confirm + one automatic retry
        coVerify(exactly = 2) { coordinator.requestChangeAlbum(any(), any()) }
    }

    @Test
    fun `opening the dialog does not wipe a pending message`() = runTest {
        album("env-a").value = selA
        coEvery { coordinator.requestChangeAlbum("env-a", treeB) } returns plan()
        coEvery { tracker.retryServerErrors("env-a", any()) } returns 0
        val vm = started(vm())
        vm.retryServerErrors()
        vm.onAlbumPicked(treeB)
        assertEquals("No files with server errors", vm.uiState.value.message)
        assertEquals(treeB, vm.uiState.value.changePlan?.newTreeUri)
    }

    @Test
    fun `re-picking the same folder while access is lost goes to the coordinator and says nothing about it being selected`() = runTest {
        album("env-a").value = selA
        scan("env-a").value = ScanState.PermissionLost
        val p = plan(tree = treeA, same = true, regrant = true)
        coEvery { coordinator.requestChangeAlbum("env-a", treeA) } returns p
        coEvery { coordinator.confirmChangeAlbum(p) } answers { scan("env-a").value = ScanState.Done(1); AlbumChangeResult.Applied(ScanState.Done(1)) }
        val vm = started(vm())
        vm.onAlbumPicked(treeA)
        assertNull(vm.uiState.value.message) // not "already selected"
        assertNull(vm.uiState.value.changePlan)
        assertTrue(vm.uiState.value.scan is ScanState.Done)
        coVerify(exactly = 0) { coordinator.selectAlbum(any(), any()) }
    }

    // ---- busy / double taps (deferred dispatcher) -----------------------------------------------------------------

    @Test
    fun `with a deferred dispatcher a double confirm calls the coordinator once and cancel is ignored while busy`() = runTest {
        val std = StandardTestDispatcher(testScheduler)
        Dispatchers.setMain(std)
        album("env-a").value = selA
        val p = plan()
        val gate = CompletableDeferred<AlbumChangeResult>()
        coEvery { coordinator.requestChangeAlbum("env-a", treeB) } returns p
        coEvery { coordinator.confirmChangeAlbum(p) } coAnswers { gate.await() }
        val vm = vm(std)
        backgroundScope.launch(UnconfinedTestDispatcher(testScheduler)) { vm.uiState.collect { } }
        runCurrent()
        vm.onAlbumPicked(treeB)
        vm.onAlbumPicked(treeB) // dropped: busy was set synchronously
        runCurrent()
        coVerify(exactly = 1) { coordinator.requestChangeAlbum(any(), any()) }
        assertFalse(vm.uiState.value.busy)

        vm.confirmChangeAlbum()
        vm.confirmChangeAlbum() // dropped
        runCurrent()
        coVerify(exactly = 1) { coordinator.confirmChangeAlbum(p) }
        assertTrue(vm.uiState.value.busy)
        vm.dismissChangeAlbum()
        runCurrent()
        assertEquals(p, vm.uiState.value.changePlan)

        gate.complete(AlbumChangeResult.Applied(ScanState.Done(1)))
        runCurrent()
        assertFalse(vm.uiState.value.busy)
        assertNull(vm.uiState.value.changePlan)
    }

    @Test
    fun `the rejected list shows loading until the first emission, not an empty list`() = runTest {
        val std = StandardTestDispatcher(testScheduler)
        Dispatchers.setMain(std)
        val shared = MutableSharedFlow<List<TrackedFileEntity>>()
        rejectedOverride["env-a"] = shared
        val vm = vm(std)
        backgroundScope.launch(UnconfinedTestDispatcher(testScheduler)) { vm.uiState.collect { } }
        runCurrent()
        vm.showRejected()
        runCurrent()
        assertTrue(vm.uiState.value.rejectedLoading)
        assertTrue(vm.uiState.value.rejected.isEmpty())
        shared.emit(listOf(entity("a", RejectionReasons.SERVER_ERROR)))
        runCurrent()
        assertFalse(vm.uiState.value.rejectedLoading)
        assertEquals(1, vm.uiState.value.rejected.size)
    }

    @Test
    fun `a failed scan keeps the counts and shows the reason`() = runTest {
        album("env-a").value = selA
        counts("env-a").value = TrackerCounts(1, 2, 0, 1)
        scan("env-a").value = ScanState.Failed(ScanFailure.LISTING_INCOMPLETE)
        val vm = started(vm())
        assertEquals(2, vm.uiState.value.counts.supported)
        assertTrue(scanIsProblem(vm.uiState.value.scan))
    }
}
