package com.memebrowser.app.data.album

import androidx.datastore.preferences.core.PreferenceDataStoreFactory
import com.memebrowser.app.data.tracker.FakeTrackerDao
import com.memebrowser.app.data.tracker.ScannedFile
import com.memebrowser.app.data.tracker.TrackerCounts
import com.memebrowser.app.data.tracker.TrackerRepository
import com.memebrowser.app.data.tracker.TrackerSettingsStore
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.async
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.yield
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

class AlbumSyncCoordinatorTest {

    @get:Rule
    val tmp = TemporaryFolder()

    private val dao = FakeTrackerDao()
    private val access = FakeAlbumAccess()
    private val scanner = FakeAlbumScanner()
    private val gate = RecordingSyncGate { dao.all.size }
    private val general = "builtin-general"
    private val metal = "builtin-metal"
    private val albumA = "content://x/tree/primary%3ADCIM%2FCamera"
    private val albumB = "content://x/tree/primary%3APictures%2FMemes"

    private lateinit var tracker: TrackerRepository

    private fun coordinator(): AlbumSyncCoordinator {
        val store = TrackerSettingsStore(PreferenceDataStoreFactory.create(produceFile = { File(tmp.root, "s.preferences_pb") }))
        tracker = TrackerRepository(dao, store)
        return AlbumSyncCoordinator(tracker, scanner, access, gate, { listOf(general, metal) }, clock = { 1234L })
    }

    private fun file(id: String, mime: String = "image/jpeg") = ScannedFile(id, "$id.jpg", mime, 10, 1)
    private fun listing(vararg ids: String) = AlbumScanResult.Complete(ids.map { file(it) })
    /** The tracker's DataStore reads on a real executor, so a plain yield() is not enough to reach the scanner. */
    private suspend fun awaitScannerCalls(n: Int) {
        repeat(5000) {
            if (scanner.calls.size >= n) return
            yield()
            Thread.sleep(1)
        }
        error("scanner was not reached")
    }

    private suspend fun counts(env: String) = tracker.observeCounts(env).first()

    @Test
    fun `first selection persists grant, stores album and name, then scans`() = runTest {
        val c = coordinator()
        scanner.result = { listing("a", "b") }
        val r = c.selectAlbum(general, albumA)
        assertEquals(AlbumChangeResult.Applied(ScanState.Done(1234L)), r)
        assertTrue(albumA in access.granted)
        assertEquals("Camera", c.album(general).first()?.displayName)
        assertEquals(albumA, c.album(general).first()?.treeUri)
        assertEquals(2, counts(general).pending)
        assertEquals(ScanState.Done(1234L), c.scanState(general).value)
    }

    @Test
    fun `first selection with a refused grant stores nothing`() = runTest {
        val c = coordinator()
        access.persistFails = true
        assertEquals(AlbumChangeResult.GrantFailed, c.selectAlbum(general, albumA))
        assertNull(c.album(general).first())
        assertTrue(scanner.calls.isEmpty())
    }

    @Test
    fun `selectAlbum with a different album stored needs confirmation and changes nothing`() = runTest {
        val c = coordinator()
        scanner.result = { listing("a") }
        c.selectAlbum(general, albumA)
        assertEquals(AlbumChangeResult.RequiresConfirmation, c.selectAlbum(general, albumB))
        assertEquals(albumA, c.album(general).first()?.treeUri)
        assertEquals(1, counts(general).pending)
    }

    @Test
    fun `plan reports what would be wiped and wipes nothing`() = runTest {
        val c = coordinator()
        scanner.result = { listing("a", "b", "c") }
        c.selectAlbum(general, albumA)
        tracker.markUploaded(general, albumA, listOf("a"))
        tracker.markRejected(general, albumA, "b", "bad content")
        val plan = c.requestChangeAlbum(general, albumB)
        assertEquals(1, plan.uploaded)
        assertEquals(1, plan.pending)
        assertEquals(1, plan.rejected)
        assertTrue(plan.wipesData)
        assertFalse(plan.isNoOp)
        assertEquals("Memes", plan.newDisplayName)
        assertEquals(3, dao.all.size)
        assertEquals(albumA, c.album(general).first()?.treeUri)
        assertTrue(albumB !in access.granted)
    }

    @Test
    fun `plan flags the app's own folder`() = runTest {
        val c = coordinator()
        val own = "content://x/tree/primary%3APictures%2FMemesBrowser-general"
        assertTrue(c.requestChangeAlbum(general, own).looksLikeAppOwnFolder)
        assertFalse(c.requestChangeAlbum(general, albumA).looksLikeAppOwnFolder)
    }

    @Test
    fun `first selection plan wipes nothing`() = runTest {
        val plan = coordinator().requestChangeAlbum(general, albumA)
        assertFalse(plan.wipesData)
        assertFalse(plan.isNoOp)
    }

    @Test
    fun `confirm cancels the sync before the wipe, wipes only this environment, swaps the grant`() = runTest {
        val c = coordinator()
        scanner.result = { listing("a", "b") }
        c.selectAlbum(general, albumA)
        c.selectAlbum(metal, albumA) // shares the tree with metal: its grant must survive
        scanner.result = { if (it == albumB) listing("z") else listing("a", "b") }
        val plan = c.requestChangeAlbum(general, albumB)

        val r = c.confirmChangeAlbum(plan)

        assertEquals(listOf(general), gate.cancelled)
        assertEquals("rows of both environments still existed when the gate was called", listOf(4), gate.probeAtCancel)
        assertTrue(r is AlbumChangeResult.Applied)
        assertEquals(albumB, c.album(general).first()?.treeUri)
        assertEquals("Memes", c.album(general).first()?.displayName)
        assertEquals(TrackerCounts(0, 1, 0, 1), counts(general))
        assertEquals(TrackerCounts(0, 2, 0, 2), counts(metal))
        assertEquals(albumA, c.album(metal).first()?.treeUri)
        assertTrue(albumB in access.granted)
        assertTrue("old tree is still used by metal", albumA in access.granted)
        assertTrue(access.released.isEmpty())
    }

    @Test
    fun `old grant is released when no other environment uses it`() = runTest {
        val c = coordinator()
        scanner.result = { listing("a") }
        c.selectAlbum(general, albumA)
        c.confirmChangeAlbum(c.requestChangeAlbum(general, albumB))
        assertEquals(listOf(albumA), access.released)
        assertFalse(albumA in access.granted)
    }

    @Test
    fun `confirm with a refused grant wipes nothing and does not call the gate`() = runTest {
        val c = coordinator()
        scanner.result = { listing("a") }
        c.selectAlbum(general, albumA)
        access.persistFails = true
        val r = c.confirmChangeAlbum(c.requestChangeAlbum(general, albumB))
        assertEquals(AlbumChangeResult.GrantFailed, r)
        assertTrue(gate.cancelled.isEmpty())
        assertEquals(1, dao.all.size)
        assertEquals(albumA, c.album(general).first()?.treeUri)
    }

    @Test
    fun `same album again is a no-op without wipe, gate or scan`() = runTest {
        val c = coordinator()
        scanner.result = { listing("a") }
        c.selectAlbum(general, albumA)
        tracker.markUploaded(general, albumA, listOf("a"))
        val scansBefore = scanner.calls.size

        val plan = c.requestChangeAlbum(general, albumA)
        assertTrue(plan.isNoOp)
        assertFalse(plan.wipesData)
        assertEquals(AlbumChangeResult.Unchanged, c.confirmChangeAlbum(plan))
        assertEquals(AlbumChangeResult.Unchanged, c.selectAlbum(general, albumA))

        assertEquals(scansBefore, scanner.calls.size)
        assertTrue(gate.cancelled.isEmpty())
        assertEquals(1, counts(general).uploaded)
    }

    @Test
    fun `same album with a lost grant is re-granted and rescanned without a wipe`() = runTest {
        val c = coordinator()
        scanner.result = { listing("a") }
        c.selectAlbum(general, albumA)
        tracker.markUploaded(general, albumA, listOf("a"))
        access.granted.clear()

        val plan = c.requestChangeAlbum(general, albumA)
        assertTrue(plan.needsRegrant)
        assertFalse(plan.isNoOp)
        assertFalse(plan.wipesData)
        val r = c.confirmChangeAlbum(plan)

        assertTrue(r is AlbumChangeResult.Applied)
        assertTrue(albumA in access.granted)
        assertTrue(gate.cancelled.isEmpty())
        assertEquals(1, counts(general).uploaded)
    }

    @Test
    fun `permission lost surfaces a state and does not touch the tracker`() = runTest {
        val c = coordinator()
        scanner.result = { listing("a", "b") }
        c.selectAlbum(general, albumA)
        val txBefore = dao.transactionCount
        scanner.result = { AlbumScanResult.PermissionLost }

        assertEquals(ScanState.PermissionLost, c.refreshScan(general))

        assertEquals(ScanState.PermissionLost, c.scanState(general).value)
        assertEquals(txBefore, dao.transactionCount)
        assertEquals(2, counts(general).pending)
        assertEquals(albumA, c.album(general).first()?.treeUri)
    }

    @Test
    fun `incomplete listing never reaches syncScan`() = runTest {
        val c = coordinator()
        scanner.result = { listing("a", "b") }
        c.selectAlbum(general, albumA)
        val txBefore = dao.transactionCount
        scanner.result = { AlbumScanResult.Incomplete("provider still loading") }

        assertEquals(ScanState.Failed(ScanFailure.LISTING_INCOMPLETE), c.refreshScan(general))

        assertEquals(txBefore, dao.transactionCount)
        assertEquals("rows are not flagged absent", 2, counts(general).pending)
    }

    @Test
    fun `empty complete listing is passed with allowEmpty and zeroes Y`() = runTest {
        val c = coordinator()
        scanner.result = { listing("a", "b") }
        c.selectAlbum(general, albumA)
        tracker.markUploaded(general, albumA, listOf("a"))
        scanner.result = { AlbumScanResult.Complete(emptyList()) }

        assertEquals(ScanState.Done(1234L), c.refreshScan(general))

        assertEquals(TrackerCounts(uploaded = 1, supported = 0, rejected = 0, pending = 0), counts(general))
    }

    @Test
    fun `refresh without an album fails with NO_ALBUM and never scans`() = runTest {
        val c = coordinator()
        assertEquals(ScanState.Failed(ScanFailure.NO_ALBUM), c.refreshScan(general))
        assertTrue(scanner.calls.isEmpty())
    }

    @Test
    fun `overlapping refreshes of one environment run one scan and share the result`() = runTest {
        val c = coordinator()
        scanner.result = { listing("a") }
        c.selectAlbum(general, albumA)
        scanner.calls.clear()
        val release = CompletableDeferred<Unit>()
        scanner.hold = { release }

        val first = async { c.refreshScan(general) }
        awaitScannerCalls(1)
        assertEquals(ScanState.Scanning, c.scanState(general).value)
        val second = async { c.refreshScan(general) }
        yield()
        release.complete(Unit)

        assertEquals(ScanState.Done(1234L), first.await())
        assertEquals(ScanState.Done(1234L), second.await())
        assertEquals(1, scanner.calls.size)
        // The guard is released afterwards: a later call scans again.
        scanner.hold = null
        c.refreshScan(general)
        assertEquals(2, scanner.calls.size)
    }

    @Test
    fun `cancelled refresh resets the state and the guard`() = runTest {
        val c = coordinator()
        scanner.result = { listing("a") }
        c.selectAlbum(general, albumA)
        scanner.calls.clear()
        scanner.hold = { CompletableDeferred() } // never completes
        val job = async { c.refreshScan(general) }
        awaitScannerCalls(1)
        val waiter = async { c.refreshScan(general) }
        yield()
        job.cancel()
        yield()

        assertEquals(ScanState.Failed(ScanFailure.CANCELLED), waiter.await())
        assertEquals(ScanState.Idle, c.scanState(general).value)
        scanner.hold = null
        assertEquals(ScanState.Done(1234L), c.refreshScan(general))
    }

    @Test
    fun `environments scan independently`() = runTest {
        val c = coordinator()
        scanner.result = { listing("a") }
        c.selectAlbum(general, albumA)
        c.selectAlbum(metal, albumB)
        scanner.calls.clear()
        val release = CompletableDeferred<Unit>()
        scanner.hold = { if (it == albumA) release else null }

        val slow = async { c.refreshScan(general) }
        awaitScannerCalls(1)
        assertEquals(ScanState.Done(1234L), c.refreshScan(metal))
        assertEquals(ScanState.Scanning, c.scanState(general).value)
        assertEquals(ScanState.Done(1234L), c.scanState(metal).value)
        release.complete(Unit)
        slow.await()
        assertEquals(listOf(albumA, albumB), scanner.calls)
    }

    @Test
    fun `permission lost in one environment leaves the other untouched`() = runTest {
        val c = coordinator()
        scanner.result = { listing("a") }
        c.selectAlbum(general, albumA)
        c.selectAlbum(metal, albumB)
        scanner.result = { if (it == albumA) AlbumScanResult.PermissionLost else listing("a") }
        c.refreshScan(general)
        c.refreshScan(metal)
        assertEquals(ScanState.PermissionLost, c.scanState(general).value)
        assertEquals(ScanState.Done(1234L), c.scanState(metal).value)
    }
}
