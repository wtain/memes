package com.memebrowser.app.data.sync

import com.memebrowser.app.data.album.AlbumScanResult
import com.memebrowser.app.data.album.AlbumSyncCoordinator
import com.memebrowser.app.data.album.FakeAlbumScanner
import com.memebrowser.app.data.album.SyncGate
import com.memebrowser.app.data.tracker.ScannedFile
import com.memebrowser.app.data.tracker.TrackedFileState.PENDING
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.async
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.yield
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

@OptIn(ExperimentalCoroutinesApi::class)
class SyncGateTest {

    @get:Rule
    val tmp = TemporaryFolder()

    private val env = "builtin-general"

    @Test
    fun `cancelAndAwait without a run returns at once`() = runTest {
        val h = SyncHarness(testScheduler, tmp.root)
        EngineSyncGate(h.engine).cancelAndAwait(env)
        assertTrue(h.engine.state(env).value is SyncState.Idle)
    }

    @Test
    fun `cancelAndAwait stops the run before returning`() = runTest {
        val h = SyncHarness(testScheduler, tmp.root, batchSize = 2)
        h.seed(env, 6)
        val hold = CompletableDeferred<Unit>()
        h.uploader.handler = { _, _ -> hold.await(); error("unreachable") }
        h.engine.start(env); runCurrent()
        assertTrue(h.engine.isRunning(env))

        EngineSyncGate(h.engine).cancelAndAwait(env)
        assertTrue(h.engine.state(env).value is SyncState.Stopped)
        assertEquals(6, h.count(env, PENDING))
    }

    @Test
    fun `confirmChangeAlbum stops the sync before the wipe`() = runTest {
        val h = SyncHarness(testScheduler, tmp.root, batchSize = 2)
        h.seed(env, 6)
        val hold = CompletableDeferred<Unit>()
        h.uploader.handler = { _, _ -> hold.await(); error("unreachable") }

        val stateAtGate = mutableListOf<SyncState>()
        val rowsAtGate = mutableListOf<Int>()
        val real = EngineSyncGate(h.engine)
        val gate = object : SyncGate {
            override suspend fun cancelAndAwait(environmentId: String) {
                real.cancelAndAwait(environmentId)
                stateAtGate += h.engine.state(environmentId).value
                rowsAtGate += h.dao.all.size // rows still there: the wipe has not happened yet
            }
        }
        val newAlbum = "content://x/tree/primary%3APictures%2FOther"
        val access = h.access
        val scanner = FakeAlbumScanner().apply {
            result = { AlbumScanResult.Complete(listOf(ScannedFile("n1", "n1.jpg", "image/jpeg", 1, 1))) }
        }
        val coordinator = AlbumSyncCoordinator(h.tracker, scanner, access, gate, { listOf(env) }, clock = { 1L })
        h.tracker.setAlbum(env, TREE, "Camera")

        h.engine.start(env); runCurrent()
        val plan = coordinator.requestChangeAlbum(env, newAlbum)
        val result = async { coordinator.confirmChangeAlbum(plan) }
        while (!result.isCompleted) { runCurrent(); yield(); Thread.sleep(1) }
        result.await()
        advanceUntilIdle()

        assertTrue(stateAtGate.single() is SyncState.Stopped)
        assertEquals(6, rowsAtGate.single())
        assertEquals(listOf("n1"), h.rows(env).map { it.documentId }) // old rows wiped, new album scanned
    }
}
