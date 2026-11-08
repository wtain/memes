package com.memebrowser.app.data.sync

import com.memebrowser.app.data.model.FailedFile
import com.memebrowser.app.data.repository.UploadAttempt
import com.memebrowser.app.data.tracker.TrackedFileState.PENDING
import com.memebrowser.app.data.tracker.TrackedFileState.REJECTED
import com.memebrowser.app.data.tracker.TrackedFileState.UPLOADED
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.IOException

@OptIn(ExperimentalCoroutinesApi::class)
class SyncEngineTest {

    @get:Rule
    val tmp = TemporaryFolder()

    private val env = "builtin-general"
    private val other = "builtin-metal"

    private fun kotlinx.coroutines.test.TestScope.harness(batch: Int = 50) = SyncHarness(testScheduler, tmp.root, batch)

    private inline fun <reified T : SyncState> SyncHarness.stateOf(e: String = env): T {
        val s = engine.state(e).value
        assertTrue("state was $s", s is T)
        return s as T
    }

    // ---- happy path ---------------------------------------------------------------------------------------

    @Test
    fun `125 files at batch 50 go in batches of 50, 50, 25 and X grows per batch`() = runTest {
        val h = harness()
        h.seed(env, 125)
        val uploadedAfterEachCall = mutableListOf<Int>()
        h.uploader.handler = { e, files ->
            uploadedAfterEachCall += h.count(e, UPLOADED) // before this batch is applied
            FakeUploader.acceptAll(files)
        }
        assertTrue(h.engine.start(env))
        advanceUntilIdle()
        assertEquals(listOf(50, 50, 25), h.uploader.calls.map { it.second.size })
        assertEquals(listOf(0, 50, 100), uploadedAfterEachCall)
        val done = h.stateOf<SyncState.Finished>()
        assertEquals(SyncSummary(uploaded = 125), done.summary)
        assertEquals(125, h.count(env, UPLOADED))
        assertEquals(0, h.count(env, PENDING))
    }

    @Test
    fun `batch size 1 and 7`() = runTest {
        val h = harness(batch = 1)
        h.seed(env, 3)
        h.engine.start(env); advanceUntilIdle()
        assertEquals(listOf(1, 1, 1), h.uploader.calls.map { it.second.size })

        val h2 = harness(batch = 7)
        h2.seed(env, 15)
        h2.engine.start(env); advanceUntilIdle()
        assertEquals(listOf(7, 7, 1), h2.uploader.calls.map { it.second.size })
    }

    @Test
    fun `batch size is read before every batch`() = runTest {
        val h = harness(batch = 2)
        h.seed(env, 7)
        h.uploader.handler = { _, files -> h.settings.batchSize = 4; FakeUploader.acceptAll(files) }
        h.engine.start(env); advanceUntilIdle()
        assertEquals(listOf(2, 4, 1), h.uploader.calls.map { it.second.size })
    }

    @Test
    fun `no pending files finishes at once with an empty summary`() = runTest {
        val h = harness()
        h.seed(env, 0)
        h.engine.start(env); advanceUntilIdle()
        assertEquals(SyncSummary(), h.stateOf<SyncState.Finished>().summary)
        assertTrue(h.uploader.calls.isEmpty())
    }

    @Test
    fun `without album or grant the run fails clearly and sends nothing`() = runTest {
        val h = harness()
        h.engine.start(env); advanceUntilIdle()
        assertEquals(SyncError.NO_ALBUM, h.stateOf<SyncState.Failed>().error)

        h.seed(env, 2)
        h.access.granted.clear()
        h.engine.start(env); advanceUntilIdle()
        assertEquals(SyncError.PERMISSION_LOST, h.stateOf<SyncState.Failed>().error)
        assertEquals(2, h.count(env, PENDING))
        assertTrue(h.uploader.calls.isEmpty())
    }

    @Test
    fun `duplicate counts as uploaded`() = runTest {
        val h = harness()
        h.seed(env, 3)
        h.uploader.handler = { _, files -> FakeUploader.acceptAll(files, status = "duplicate") }
        h.engine.start(env); advanceUntilIdle()
        assertEquals(3, h.count(env, UPLOADED))
        assertEquals(SyncSummary(uploaded = 0, duplicates = 3), h.stateOf<SyncState.Finished>().summary)
    }

    @Test
    fun `files added by a scan during the run join the same run`() = runTest {
        val h = harness(batch = 5)
        h.seed(env, 5)
        h.uploader.handler = { _, files ->
            if (h.uploader.calls.size == 1) h.seed(env, 8) // a scan finds 3 more files meanwhile
            FakeUploader.acceptAll(files)
        }
        h.engine.start(env); advanceUntilIdle()
        assertEquals(8, h.count(env, UPLOADED))
        assertEquals(listOf(5, 3), h.uploader.calls.map { it.second.size })
    }

    // ---- stop ---------------------------------------------------------------------------------------------

    @Test
    fun `stop mid-run keeps the unfinished batch pending and nothing is double-marked`() = runTest {
        val h = harness(batch = 2)
        h.seed(env, 6)
        val hold = CompletableDeferred<Unit>()
        h.uploader.handler = { _, files ->
            if (h.uploader.calls.size == 2) hold.await() // the second batch hangs on the wire
            FakeUploader.acceptAll(files)
        }
        h.engine.start(env); runCurrent()
        assertTrue(h.engine.isRunning(env))
        h.engine.stop(env)
        assertTrue(h.engine.state(env).value is SyncState.Stopping)
        advanceUntilIdle()
        val stopped = h.stateOf<SyncState.Stopped>()
        assertEquals(SyncSummary(uploaded = 2), stopped.summary)
        assertEquals(2, h.count(env, UPLOADED))
        assertEquals(4, h.count(env, PENDING))
        assertFalse(h.engine.isRunning(env))

        // The next run sends the four remaining files, once each.
        h.uploader.handler = { _, files -> FakeUploader.acceptAll(files) }
        h.engine.start(env); advanceUntilIdle()
        assertEquals(6, h.count(env, UPLOADED))
        assertEquals(listOf("f0", "f1", "f2", "f3", "f2", "f3", "f4", "f5").sorted(), h.uploader.calls.flatMap { it.second }.sorted())
    }

    @Test
    fun `stop right after start still ends in Stopped`() = runTest {
        val h = harness()
        h.seed(env, 3)
        h.engine.start(env)
        h.engine.stop(env)
        advanceUntilIdle()
        assertTrue(h.engine.state(env).value is SyncState.Stopped)
        assertFalse(h.engine.isRunning(env))
        assertEquals(3, h.count(env, PENDING))
    }

    @Test
    fun `a received response is recorded even when stop arrives right then`() = runTest {
        val h = harness(batch = 2)
        h.seed(env, 4)
        h.uploader.handler = { e, files ->
            h.engine.stop(e) // cancelled, then the response still arrives
            FakeUploader.acceptAll(files)
        }
        h.engine.start(env); advanceUntilIdle()
        assertEquals(2, h.count(env, UPLOADED))
        assertEquals(SyncSummary(uploaded = 2), h.stateOf<SyncState.Stopped>().summary)
    }

    @Test
    fun `start twice is a single run`() = runTest {
        val h = harness()
        h.seed(env, 3)
        assertTrue(h.engine.start(env))
        assertFalse(h.engine.start(env))
        advanceUntilIdle()
        assertEquals(1, h.uploader.calls.size)
        assertTrue(h.engine.start(env)) // after it ended a new run is allowed
        advanceUntilIdle()
        assertEquals(1, h.uploader.calls.size) // nothing pending: no request
    }

    @Test
    fun `two environments sync independently and simultaneously`() = runTest {
        val h = harness(batch = 2)
        h.seed(env, 4)
        h.seed(other, 4, prefix = "m")
        val hold = CompletableDeferred<Unit>()
        h.uploader.handler = { e, files -> if (e == env) hold.await(); FakeUploader.acceptAll(files) }
        h.engine.start(env); h.engine.start(other); runCurrent()
        assertTrue(h.engine.state(other).value is SyncState.Finished)
        assertTrue(h.engine.isRunning(env))
        h.engine.stop(other) // no run: nothing happens
        assertTrue(h.engine.state(other).value is SyncState.Finished)
        hold.complete(Unit); advanceUntilIdle()
        assertEquals(4, h.count(env, UPLOADED))
        assertEquals(4, h.count(other, UPLOADED))
    }

    // ---- response mapping ---------------------------------------------------------------------------------

    @Test
    fun `results are matched by index, whatever the response order`() = runTest {
        val h = harness()
        h.seed(env, 3)
        h.uploader.handler = { _, _ ->
            response(
                uploaded = listOf(accepted(2, "x.jpg"), accepted(0, "x.jpg")), // same name on purpose
                failed = listOf(FailedFile("x.jpg", "File too large", 1)),
            )
        }
        h.engine.start(env); advanceUntilIdle()
        assertEquals(UPLOADED, h.row(env, "f0").state)
        assertEquals(REJECTED, h.row(env, "f1").state)
        assertEquals("File too large", h.row(env, "f1").rejectionReason)
        assertEquals(UPLOADED, h.row(env, "f2").state)
    }

    @Test
    fun `a rejected file is not sent again on the next start`() = runTest {
        val h = harness()
        h.seed(env, 2)
        h.uploader.handler = { _, _ ->
            response(listOf(accepted(0, "f0.jpg")), listOf(FailedFile("f1.jpg", "Unsupported file type", 1)))
        }
        h.engine.start(env); advanceUntilIdle()
        assertEquals(SyncSummary(uploaded = 1, rejected = 1), h.stateOf<SyncState.Finished>().summary)
        h.uploader.calls.clear()
        h.engine.start(env); advanceUntilIdle()
        assertTrue(h.uploader.calls.isEmpty())
        assertEquals(REJECTED, h.row(env, "f1").state)
    }

    @Test
    fun `a backend without per-file index fails the run and marks nothing uploaded`() = runTest {
        val h = harness()
        h.seed(env, 3)
        h.uploader.handler = { _, _ ->
            response(
                uploaded = listOf(accepted(0, "f2.jpg", status = "ok").copy(index = null)),
                failed = emptyList(),
            )
        }
        h.engine.start(env); advanceUntilIdle()
        val f = h.stateOf<SyncState.Failed>()
        assertEquals(SyncError.SERVER, f.error)
        assertEquals("backend too old: update the backend before syncing", f.message)
        assertEquals(3, h.count(env, PENDING))
        assertEquals(1, h.uploader.calls.size)
    }

    @Test
    fun `status ok with an index is also an old backend`() = runTest {
        val h = harness()
        h.seed(env, 2)
        h.uploader.handler = { _, _ -> response(listOf(accepted(0, "f0.jpg", status = "ok"))) }
        h.engine.start(env); advanceUntilIdle()
        assertEquals(SyncError.SERVER, h.stateOf<SyncState.Failed>().error)
        assertEquals(0, h.count(env, UPLOADED))
    }

    @Test
    fun `an unknown status or out-of-range index never marks a file uploaded`() = runTest {
        val h = harness()
        h.seed(env, 2)
        h.uploader.handler = { _, _ ->
            response(listOf(accepted(0, "f0.jpg", status = "queued"), accepted(9, "f1.jpg")))
        }
        h.engine.start(env); advanceUntilIdle()
        assertEquals(0, h.count(env, UPLOADED))
        assertEquals(2, h.count(env, PENDING))
    }

    @Test
    fun `partial coverage leaves the uncovered files pending and the run continues`() = runTest {
        val h = harness(batch = 2)
        h.seed(env, 4)
        h.uploader.handler = { _, files ->
            // f0/f2 are answered, f1 never is; later calls answer everything
            if (h.uploader.calls.size == 1) response(listOf(accepted(0, files[0].displayName))) else FakeUploader.acceptAll(files)
        }
        h.engine.start(env); advanceUntilIdle()
        assertEquals(4, h.count(env, UPLOADED))
        assertTrue(h.engine.state(env).value is SyncState.Finished)
    }

    @Test
    fun `two batches in a row without any result fail the run`() = runTest {
        val h = harness()
        h.seed(env, 3)
        h.uploader.handler = { _, _ -> response(emptyList()) }
        h.engine.start(env); advanceUntilIdle()
        assertEquals(SyncError.UNEXPECTED, h.stateOf<SyncState.Failed>().error)
        assertEquals(2, h.uploader.calls.size)
        assertEquals(3, h.count(env, PENDING))
    }

    @Test
    fun `an unreadable file is rejected locally and not sent`() = runTest {
        val h = harness()
        h.seed(env, 3)
        h.source.unavailable += "f1"
        h.engine.start(env); advanceUntilIdle()
        assertEquals(listOf("f0", "f2"), h.uploader.calls.single().second)
        assertEquals(REJECTED, h.row(env, "f1").state)
        assertEquals("file_unavailable", h.row(env, "f1").rejectionReason)
        assertEquals(SyncSummary(uploaded = 2, rejected = 1), h.stateOf<SyncState.Finished>().summary)
    }

    @Test
    fun `unreadable files with a grant lost mid-run fail the run and reject nothing`() = runTest {
        val h = harness(batch = 2)
        h.seed(env, 4)
        h.source.unavailable += setOf("f2", "f3")
        h.uploader.handler = { _, files ->
            h.access.granted.clear() // the user revokes the grant while the first batch is on the wire
            FakeUploader.acceptAll(files)
        }
        h.engine.start(env); advanceUntilIdle()
        assertEquals(SyncError.PERMISSION_LOST, h.stateOf<SyncState.Failed>().error)
        assertEquals(2, h.count(env, UPLOADED))
        assertEquals(2, h.count(env, PENDING))
        assertEquals(0, h.count(env, REJECTED))
    }

    // ---- error policy -------------------------------------------------------------------------------------

    @Test
    fun `network failure fails the run and keeps the files pending`() = runTest {
        val h = harness()
        h.seed(env, 3)
        h.uploader.handler = { _, _ -> UploadAttempt.NetworkError("timeout") }
        h.engine.start(env); advanceUntilIdle()
        val f = h.stateOf<SyncState.Failed>()
        assertEquals(SyncError.NETWORK, f.error)
        assertEquals(3, h.count(env, PENDING))
        assertEquals(1, h.uploader.calls.size) // no automatic retry
    }

    @Test
    fun `http status classes map to their errors and never mark anything`() = runTest {
        val cases = listOf(
            502 to SyncError.SERVER, 503 to SyncError.INBOX_FULL_OR_UNWRITABLE,
            507 to SyncError.DISK_LOW, 422 to SyncError.UNEXPECTED, 400 to SyncError.UNEXPECTED, 413 to SyncError.UNEXPECTED,
        )
        for ((code, error) in cases) {
            val h = harness()
            h.seed(env, 2)
            h.uploader.handler = { _, _ -> UploadAttempt.HttpError(code, "body", null) }
            h.engine.start(env); advanceUntilIdle()
            val f = h.stateOf<SyncState.Failed>()
            assertEquals("code $code", error, f.error)
            assertEquals("code $code", 2, h.count(env, PENDING))
            if (error == SyncError.UNEXPECTED) assertTrue(f.message.contains("$code"))
        }
    }

    @Test
    fun `unexpected attempt fails the run`() = runTest {
        val h = harness()
        h.seed(env, 2)
        h.uploader.handler = { _, _ -> UploadAttempt.Unexpected("bad body") }
        h.engine.start(env); advanceUntilIdle()
        assertEquals(SyncError.UNEXPECTED, h.stateOf<SyncState.Failed>().error)
    }

    @Test
    fun `an exception inside the engine becomes Failed UNEXPECTED, not a crash`() = runTest {
        val h = harness()
        h.seed(env, 2)
        h.uploader.handler = { _, _ -> throw IllegalStateException("boom") }
        h.engine.start(env); advanceUntilIdle()
        assertEquals("boom", h.stateOf<SyncState.Failed>().message)
    }

    @Test
    fun `429 waits 60 seconds then retries the same batch`() = runTest {
        val h = harness(batch = 2)
        h.seed(env, 4)
        h.uploader.handler = { _, files ->
            if (h.uploader.calls.size == 2) UploadAttempt.HttpError(429, "slow", null) else FakeUploader.acceptAll(files)
        }
        h.engine.start(env); runCurrent()
        val w = h.stateOf<SyncState.Waiting>()
        assertEquals(WaitReason.RATE_LIMITED, w.reason)
        assertEquals(testScheduler.currentTime + 60_000, w.resumeAt)
        assertEquals(SyncSummary(uploaded = 2), w.summary)
        advanceTimeBy(59_999); runCurrent()
        assertTrue(h.engine.state(env).value is SyncState.Waiting)
        advanceTimeBy(1); runCurrent()
        advanceUntilIdle()
        assertTrue(h.engine.state(env).value is SyncState.Finished)
        assertEquals(listOf(listOf("f0", "f1"), listOf("f2", "f3"), listOf("f2", "f3")), h.uploader.calls.map { it.second })
        assertEquals(4, h.count(env, UPLOADED))
    }

    @Test
    fun `a Retry-After header wins over the default wait`() = runTest {
        val h = harness()
        h.seed(env, 1)
        h.uploader.handler = { _, files ->
            if (h.uploader.calls.size == 1) UploadAttempt.HttpError(429, null, 5) else FakeUploader.acceptAll(files)
        }
        h.engine.start(env); runCurrent()
        assertEquals(testScheduler.currentTime + 5_000, h.stateOf<SyncState.Waiting>().resumeAt)
    }

    @Test
    fun `stop while waiting ends the run and keeps the files pending`() = runTest {
        val h = harness()
        h.seed(env, 2)
        h.uploader.handler = { _, _ -> UploadAttempt.HttpError(429, null, null) }
        h.engine.start(env); runCurrent()
        assertTrue(h.engine.state(env).value is SyncState.Waiting)
        h.engine.stop(env)
        advanceUntilIdle()
        assertTrue(h.engine.state(env).value is SyncState.Stopped)
        assertEquals(2, h.count(env, PENDING))
        assertEquals(1, h.uploader.calls.size)
    }

    @Test
    fun `too many consecutive 429s fail the run, a success in between resets the count`() = runTest {
        val h = harness()
        h.seed(env, 1)
        h.uploader.handler = { _, _ -> UploadAttempt.HttpError(429, null, null) }
        h.engine.start(env); advanceUntilIdle()
        assertEquals(SyncError.RATE_LIMITED, h.stateOf<SyncState.Failed>().error)
        assertEquals(SyncEngine.MAX_CONSECUTIVE_429 + 1, h.uploader.calls.size)
        assertEquals(1, h.count(env, PENDING))

        val h2 = harness(batch = 1)
        h2.seed(env, 3)
        h2.uploader.handler = { _, files ->
            // every other call is rate limited: 429, ok, 429, ok, ... never more than one in a row
            if (h2.uploader.calls.size % 2 == 1) UploadAttempt.HttpError(429, null, null) else FakeUploader.acceptAll(files)
        }
        h2.engine.start(env); advanceUntilIdle()
        assertTrue(h2.engine.state(env).value is SyncState.Finished)
        assertEquals(3, h2.count(env, UPLOADED))
    }

    @Test
    fun `a failed run can be started again`() = runTest {
        val h = harness()
        h.seed(env, 2)
        h.uploader.handler = { _, _ -> UploadAttempt.NetworkError(null) }
        h.engine.start(env); advanceUntilIdle()
        h.uploader.handler = { _, files -> FakeUploader.acceptAll(files) }
        assertTrue(h.engine.start(env)); advanceUntilIdle()
        assertEquals(2, h.count(env, UPLOADED))
    }

    @Test
    fun `an IOException type is only reached through the uploader seam`() = runTest {
        // the engine itself never sees IOException: the uploader turns it into NetworkError (RetrofitBatchUploaderTest)
        val h = harness()
        h.seed(env, 1)
        h.uploader.handler = { _, _ -> throw IOException("raw") }
        h.engine.start(env); advanceUntilIdle()
        assertEquals(SyncError.UNEXPECTED, h.stateOf<SyncState.Failed>().error)
        assertEquals(1, h.tracker.observeCounts(env).first().pending)
    }
}
