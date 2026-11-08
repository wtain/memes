package com.memebrowser.app.ui.sync

import com.memebrowser.app.data.album.AlbumChangeResult
import com.memebrowser.app.data.album.AlbumSyncCoordinator
import com.memebrowser.app.data.album.ChangeAlbumPlan
import com.memebrowser.app.data.album.ScanFailure
import com.memebrowser.app.data.album.ScanState
import com.memebrowser.app.data.repository.EnvironmentRepository
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
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.runTest
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

    private val envId = MutableStateFlow("env-a")
    private val envName = MutableStateFlow("General")
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
        every { it.selectedEnvironmentId } returns envId
        every { it.selectedEnvironmentName } returns envName
    }
    private val coordinator = mockk<AlbumSyncCoordinator>().also {
        every { it.album(any()) } answers { album(firstArg()) }
        every { it.counts(any()) } answers { counts(firstArg()) }
        every { it.scanState(any()) } answers { scan(firstArg()) }
        every { it.rejectedFiles(any()) } answers { rejected(firstArg()) }
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

    private fun vm() = SyncViewModel(environments, coordinator, engine, tracker) { now }

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

        envId.value = "env-b"
        envName.value = "Metal"
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
        envId.value = "env-b"
        assertNull(vm.uiState.value.changePlan)
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
    fun `no automatic refresh without an album or during a scan, and each environment refreshes on its own`() = runTest {
        val vm = started(vm())
        vm.onScreenStarted() // env-a has no album
        coVerify(exactly = 0) { coordinator.refreshScan(any()) }

        album("env-b").value = selA
        scan("env-b").value = ScanState.Scanning
        envId.value = "env-b"
        vm.onScreenStarted()
        coVerify(exactly = 0) { coordinator.refreshScan(any()) }

        scan("env-b").value = ScanState.Idle
        now += 1 // still inside the interval for env-b (reserved by the skipped call)
        vm.onScreenStarted()
        coVerify(exactly = 0) { coordinator.refreshScan(any()) }

        album("env-a").value = selA
        envId.value = "env-a"
        now += SyncViewModel.AUTO_REFRESH_MIN_INTERVAL_MS
        vm.onScreenStarted()
        coVerify(exactly = 1) { coordinator.refreshScan("env-a") }
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
        coEvery { coordinator.selectAlbum("env-a", treeB) } returns AlbumChangeResult.RequiresConfirmation
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
        coEvery { coordinator.confirmChangeAlbum(p1) } returns AlbumChangeResult.PlanStale
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
    fun `a failed scan keeps the counts and shows the reason`() = runTest {
        album("env-a").value = selA
        counts("env-a").value = TrackerCounts(1, 2, 0, 1)
        scan("env-a").value = ScanState.Failed(ScanFailure.LISTING_INCOMPLETE)
        val vm = started(vm())
        assertEquals(2, vm.uiState.value.counts.supported)
        assertTrue(scanIsProblem(vm.uiState.value.scan))
    }
}
