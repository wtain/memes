package com.memebrowser.app.data.sync

import com.memebrowser.app.data.repository.UploadAttempt
import com.memebrowser.app.data.tracker.TrackedFileState.PENDING
import com.memebrowser.app.data.tracker.TrackedFileState.REJECTED
import com.memebrowser.app.data.tracker.TrackedFileState.UPLOADED
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.IOException

/** Tests for the A3 review fixes (I1, I3, M1-M6, M8, M10 and the test gaps). */
@OptIn(ExperimentalCoroutinesApi::class)
class SyncEngineReviewFixesTest {

    @get:Rule
    val tmp = TemporaryFolder()

    private val env = "builtin-general"

    private fun TestScope.harness(batch: Int = 50) = SyncHarness(testScheduler, tmp.root, batch)

    private inline fun <reified T : SyncState> SyncHarness.stateOf(): T {
        val s = engine.state(env).value
        assertTrue("state was $s", s is T)
        return s as T
    }

    // ---- I1: only a vanished document is rejected locally -------------------------------------------------

    @Test
    fun `a transient read error leaves every file pending and fails the run`() = runTest {
        val h = harness()
        h.seed(env, 3)
        h.source.failing["f1"] = IOException("provider restarting")
        h.engine.start(env); advanceUntilIdle()
        val f = h.stateOf<SyncState.Failed>()
        assertEquals(SyncError.FILE_READ_ERROR, f.error)
        assertTrue(f.message.contains("f1.jpg"))
        assertEquals(3, h.count(env, PENDING))
        assertEquals(0, h.count(env, REJECTED))
        assertTrue(h.uploader.calls.isEmpty())

        // The next run, with the provider back, sends everything.
        h.source.failing.clear()
        h.engine.start(env); advanceUntilIdle()
        assertEquals(3, h.count(env, UPLOADED))
    }

    @Test
    fun `a SecurityException with the grant intact is a read error, with the grant gone it is permission lost`() = runTest {
        val h = harness()
        h.seed(env, 2)
        h.source.failing["f0"] = SecurityException("denied")
        h.engine.start(env); advanceUntilIdle()
        assertEquals(SyncError.FILE_READ_ERROR, h.stateOf<SyncState.Failed>().error)
        assertEquals(2, h.count(env, PENDING))

        h.source.onOpen = { h.access.granted.clear() }
        h.engine.start(env); advanceUntilIdle()
        assertEquals(SyncError.PERMISSION_LOST, h.stateOf<SyncState.Failed>().error)
        assertEquals(2, h.count(env, PENDING))
        assertEquals(0, h.count(env, REJECTED))
    }

    @Test
    fun `only FileNotFound rejects, even next to a transient failure that comes later`() = runTest {
        val h = harness()
        h.seed(env, 3)
        h.source.unavailable += "f0"
        h.engine.start(env); advanceUntilIdle()
        assertEquals("file_unavailable", h.row(env, "f0").rejectionReason)
        assertEquals(2, h.count(env, UPLOADED))
    }

    // ---- I3: client-side pacing ---------------------------------------------------------------------------

    @Test
    fun `12 one-file batches never put more than 9 requests into any 60 second window`() = runTest {
        val h = harness(batch = 1)
        h.seed(env, 12)
        val times = mutableListOf<Long>()
        h.uploader.handler = { _, files -> times += testScheduler.currentTime; FakeUploader.acceptAll(files) }
        val seen = h.recordStates(env, backgroundScope, testScheduler)
        h.engine.start(env); advanceUntilIdle()
        assertEquals(12, times.size)
        for (i in times.indices) {
            val inWindow = times.count { it >= times[i] && it < times[i] + 60_000 }
            assertTrue("window at ${times[i]} holds $inWindow", inWindow <= UploadPacer.MAX_REQUESTS)
        }
        assertEquals(0L, times[8])
        assertEquals(60_000L, times[9])
        assertTrue(seen.any { it is SyncState.Waiting && it.reason == WaitReason.PACING && it.attempt == 0 })
        assertTrue(h.engine.state(env).value is SyncState.Finished)
        assertEquals(12, h.count(env, UPLOADED))
    }

    @Test
    fun `stop during the pacing wait is honoured`() = runTest {
        val h = harness(batch = 1)
        h.seed(env, 12)
        h.engine.start(env)
        // 9 requests run at virtual time 0; the 10th must wait until 60_000.
        runCurrent()
        val w = h.stateOf<SyncState.Waiting>()
        assertEquals(WaitReason.PACING, w.reason)
        assertEquals(60_000L, w.resumeAt)
        h.engine.stop(env)
        advanceUntilIdle()
        assertTrue(h.engine.state(env).value is SyncState.Stopped)
        assertEquals(9, h.uploader.calls.size)
        assertEquals(3, h.count(env, PENDING))
    }

    // ---- M1: Retry-After cap ------------------------------------------------------------------------------

    @Test
    fun `a huge Retry-After is capped at 300 seconds and zero waits one second`() = runTest {
        val h = harness()
        h.seed(env, 1)
        h.uploader.handler = { _, _ -> UploadAttempt.HttpError(429, null, 100_000) }
        h.engine.start(env); runCurrent()
        assertEquals(300_000L, h.stateOf<SyncState.Waiting>().resumeAt)

        val h2 = harness()
        h2.seed(env, 1)
        h2.uploader.handler = { _, _ -> UploadAttempt.HttpError(429, null, 0) }
        h2.engine.start(env); runCurrent()
        assertEquals(1_000L, h2.stateOf<SyncState.Waiting>().resumeAt)
    }

    // ---- M2: a poison file does not block the sync ---------------------------------------------------------

    @Test
    fun `one poison file among five is rejected and the other four go through`() = runTest {
        val h = harness(batch = 5)
        h.seed(env, 5)
        h.uploader.handler = { _, files ->
            if (files.size > 1 || files.single().documentId == "f2") UploadAttempt.HttpError(500, "boom", null)
            else FakeUploader.acceptAll(files)
        }
        h.engine.start(env); advanceUntilIdle()
        val done = h.stateOf<SyncState.Finished>()
        assertEquals(SyncSummary(uploaded = 4, rejected = 1), done.summary)
        assertEquals(REJECTED, h.row(env, "f2").state)
        assertEquals("server_error", h.row(env, "f2").rejectionReason)
        assertEquals(4, h.count(env, UPLOADED))
        assertEquals(listOf(5, 1, 1, 1, 1, 1), h.uploader.calls.map { it.second.size }) // each file sent alone once

        // A later sync does not retry the rejected file.
        h.uploader.calls.clear()
        h.engine.start(env); advanceUntilIdle()
        assertTrue(h.uploader.calls.isEmpty())
    }

    @Test
    fun `three poison files in a row stop the run with Failed SERVER`() = runTest {
        val h = harness(batch = 1)
        h.seed(env, 5)
        h.uploader.handler = { _, _ -> UploadAttempt.HttpError(500, null, null) }
        h.engine.start(env); advanceUntilIdle()
        assertEquals(SyncError.SERVER, h.stateOf<SyncState.Failed>().error)
        assertEquals(3, h.uploader.calls.size)
        assertEquals(3, h.count(env, REJECTED))
        assertEquals(2, h.count(env, PENDING))

        val h2 = harness(batch = 5)
        h2.seed(env, 5)
        h2.uploader.handler = { _, _ -> UploadAttempt.HttpError(500, null, null) }
        h2.engine.start(env); advanceUntilIdle()
        assertEquals(SyncError.SERVER, h2.stateOf<SyncState.Failed>().error)
        assertEquals(1 + 3, h2.uploader.calls.size) // the batch, then three single files
        assertEquals(2, h2.count(env, PENDING))
    }

    @Test
    fun `a success between poison files resets the streak`() = runTest {
        val h = harness(batch = 1)
        h.seed(env, 6)
        h.uploader.handler = { _, files ->
            if (files.single().documentId in setOf("f0", "f1", "f3", "f4")) UploadAttempt.HttpError(500, null, null)
            else FakeUploader.acceptAll(files)
        }
        h.engine.start(env); advanceUntilIdle()
        assertTrue(h.engine.state(env).value is SyncState.Finished)
        assertEquals(4, h.count(env, REJECTED))
    }

    @Test
    fun `a non-500 failure while sending files alone follows the normal policy`() = runTest {
        val h = harness(batch = 3)
        h.seed(env, 3)
        h.uploader.handler = { _, files ->
            when {
                files.size > 1 -> UploadAttempt.HttpError(500, null, null)
                files.single().documentId == "f0" -> UploadAttempt.HttpError(500, null, null)
                else -> UploadAttempt.HttpError(507, "disk", null)
            }
        }
        h.engine.start(env); advanceUntilIdle()
        assertEquals(SyncError.DISK_LOW, h.stateOf<SyncState.Failed>().error)
        assertEquals("server_error", h.row(env, "f0").rejectionReason)
        assertEquals(2, h.count(env, PENDING))
    }

    // ---- M3 / M4 / M5: stop vs terminal results -------------------------------------------------------------

    @Test
    fun `stop that lands before a Failed result ends as Stopped with the sequence Running Stopping Stopped`() = runTest {
        val h = harness(batch = 2)
        h.seed(env, 4)
        val seen = h.recordStates(env, backgroundScope, testScheduler)
        h.uploader.handler = { e, _ -> h.engine.stop(e); UploadAttempt.NetworkError("down") } // no suspension after stop
        h.engine.start(env); advanceUntilIdle()
        val kinds = seen.map { it::class.simpleName }
        assertEquals(listOf("Idle", "Running", "Running", "Stopping", "Stopped"), kinds)
        assertEquals(4, h.count(env, PENDING))
    }

    @Test
    fun `stop that lands before a Finished result ends as Stopped and keeps the summary`() = runTest {
        val h = harness(batch = 5)
        h.seed(env, 3)
        h.uploader.handler = { e, files -> h.engine.stop(e); FakeUploader.acceptAll(files) }
        h.engine.start(env); advanceUntilIdle()
        val s = h.stateOf<SyncState.Stopped>()
        assertEquals(SyncSummary(uploaded = 3), s.summary)
        assertEquals(3, h.count(env, UPLOADED))
    }

    @Test
    fun `a plain run publishes Idle Running Running Finished and a restart after Failed goes back to Running`() = runTest {
        val h = harness()
        h.seed(env, 2)
        h.uploader.handler = { _, _ -> UploadAttempt.NetworkError("x") }
        val seen = h.recordStates(env, backgroundScope, testScheduler)
        h.engine.start(env); advanceUntilIdle()
        assertEquals(listOf("Idle", "Running", "Running", "Failed"), seen.map { it::class.simpleName })
        h.uploader.handler = { _, files -> FakeUploader.acceptAll(files) }
        h.engine.start(env); advanceUntilIdle()
        assertEquals(listOf("Idle", "Running", "Running", "Failed", "Running", "Running", "Finished"), seen.map { it::class.simpleName })
    }

    @Test
    fun `an Error thrown inside the run ends as Failed UNEXPECTED, not Stopped and not a leak`() = runTest {
        val h = harness()
        h.seed(env, 2)
        h.uploader.handler = { _, _ -> throw NotImplementedError("an Error, not an Exception") }
        h.engine.start(env); advanceUntilIdle()
        val f = h.stateOf<SyncState.Failed>()
        assertEquals(SyncError.UNEXPECTED, f.error)
        assertEquals(2, h.count(env, PENDING))
        assertFalse(h.engine.isRunning(env))
    }

    // ---- M6: access lost mid-upload ---------------------------------------------------------------------------

    @Test
    fun `access denied mid-upload is permission lost when the grant is gone, a read error otherwise`() = runTest {
        val h = harness()
        h.seed(env, 2)
        h.uploader.handler = { _, _ -> h.access.granted.clear(); UploadAttempt.AccessDenied("revoked") }
        h.engine.start(env); advanceUntilIdle()
        assertEquals(SyncError.PERMISSION_LOST, h.stateOf<SyncState.Failed>().error)
        assertEquals(2, h.count(env, PENDING))

        val h2 = harness()
        h2.seed(env, 2)
        h2.uploader.handler = { _, _ -> UploadAttempt.AccessDenied("odd") }
        h2.engine.start(env); advanceUntilIdle()
        assertEquals(SyncError.FILE_READ_ERROR, h2.stateOf<SyncState.Failed>().error)

        val h3 = harness()
        h3.seed(env, 2)
        h3.uploader.handler = { _, _ -> UploadAttempt.ReadFailed("vanished") }
        h3.engine.start(env); advanceUntilIdle()
        assertEquals(SyncError.FILE_READ_ERROR, h3.stateOf<SyncState.Failed>().error)
        assertEquals(2, h3.count(env, PENDING))
    }

    // ---- cancellation at the remaining points -------------------------------------------------------------------

    @Test
    fun `stop during the readability pre-check sends nothing and keeps everything pending`() = runTest {
        val h = harness()
        h.seed(env, 5)
        var opens = 0
        h.source.onOpen = { if (++opens == 2) h.engine.stop(env) }
        h.engine.start(env); advanceUntilIdle()
        assertTrue(h.engine.state(env).value is SyncState.Stopped)
        assertTrue(h.uploader.calls.isEmpty())
        assertEquals(5, h.count(env, PENDING))
        assertEquals(2, opens) // the loop noticed the stop at the next file
    }

    @Test
    fun `stop while the response is being applied still records the whole response`() = runTest {
        val h = harness(batch = 3)
        h.seed(env, 6)
        h.outcomes.onMarkUploaded = { h.engine.stop(env) }
        h.engine.start(env); advanceUntilIdle()
        assertTrue(h.engine.state(env).value is SyncState.Stopped)
        assertEquals(3, h.count(env, UPLOADED)) // NonCancellable: all of batch one is recorded
        assertEquals(3, h.count(env, PENDING))
        assertEquals(1, h.uploader.calls.size)
    }

    @Test
    fun `response recorded even when stop arrives, with tracker writes that suspend`() = runTest {
        val h = harness(batch = 2)
        h.seed(env, 4)
        h.uploader.handler = { e, files -> h.engine.stop(e); FakeUploader.acceptAll(files) }
        h.engine.start(env); advanceUntilIdle()
        assertEquals(2, h.count(env, UPLOADED))
        assertEquals(SyncSummary(uploaded = 2), h.stateOf<SyncState.Stopped>().summary)
        assertNull(h.dao.all.firstOrNull { it.state == UPLOADED && it.uploadedAt == null })
    }

    @Test
    fun `old backend is detected before anything is recorded`() = runTest {
        val h = harness()
        h.seed(env, 2)
        h.uploader.handler = { _, _ -> response(emptyList(), listOf(com.memebrowser.app.data.model.FailedFile("f0.jpg", "bad", null))) }
        h.engine.start(env); advanceUntilIdle()
        assertEquals(SyncError.SERVER, h.stateOf<SyncState.Failed>().error)
        assertEquals(0, h.count(env, REJECTED))
    }
}
