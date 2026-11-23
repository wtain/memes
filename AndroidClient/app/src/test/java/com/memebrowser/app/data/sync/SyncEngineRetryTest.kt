package com.memebrowser.app.data.sync

import com.memebrowser.app.data.repository.UploadAttempt
import com.memebrowser.app.data.tracker.TrackedFileState.PENDING
import com.memebrowser.app.data.tracker.TrackedFileState.UPLOADED
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

@OptIn(ExperimentalCoroutinesApi::class)
class SyncEngineRetryTest {

    @get:Rule
    val tmp = TemporaryFolder()

    private val env = "builtin-general"

    private fun TestScope.harness(batch: Int = 50) = SyncHarness(testScheduler, tmp.root, batch).also {
        it.settings.autoRetry = true
    }

    private inline fun <reified T : SyncState> SyncHarness.stateOf(): T {
        val s = engine.state(env).value
        assertTrue("state was $s", s is T)
        return s as T
    }

    /** A retry wait as published, with the virtual time at which it was published. */
    private data class Backoff(val publishedAt: Long, val w: SyncState.Waiting) {
        val delayMs get() = w.resumeAt - publishedAt
    }

    /** Records every RETRY_BACKOFF wait the engine publishes for [env]. */
    private fun TestScope.recordBackoffs(h: SyncHarness): MutableList<Backoff> {
        val seen = mutableListOf<Backoff>()
        backgroundScope.launch(UnconfinedTestDispatcher(testScheduler)) {
            h.engine.state(env).collect {
                if (it is SyncState.Waiting && it.reason == WaitReason.RETRY_BACKOFF) seen += Backoff(testScheduler.currentTime, it)
            }
        }
        return seen
    }

    private fun failingFirst(h: SyncHarness, times: Int, failure: () -> UploadAttempt) {
        h.uploader.handler = { _, files -> if (h.uploader.calls.size <= times) failure() else FakeUploader.acceptAll(files) }
    }

    @Test
    fun networkError_retriesWithGrowingDelays() = runTest {
        val h = harness()
        h.seed(env, 3)
        failingFirst(h, 3) { UploadAttempt.NetworkError("offline") }
        val backoffs = recordBackoffs(h)
        h.engine.start(env)
        advanceUntilIdle()
        assertEquals(listOf(1, 2, 3), backoffs.map { it.w.attempt })
        assertEquals(listOf(15_000L, 30_000L, 60_000L), backoffs.map { it.delayMs })
        assertEquals(SyncSummary(uploaded = 3), h.stateOf<SyncState.Finished>().summary)
        assertEquals(3, h.count(env, UPLOADED))
        assertEquals(4, h.uploader.calls.size)
    }

    @Test
    fun attemptsAreCapped() = runTest {
        val h = harness(batch = 2)
        h.seed(env, 4)
        h.uploader.handler = { _, files ->
            if (h.uploader.calls.size == 1) FakeUploader.acceptAll(files) else UploadAttempt.NetworkError("offline")
        }
        val backoffs = recordBackoffs(h)
        h.engine.start(env)
        advanceUntilIdle()
        assertEquals((1..8).toList(), backoffs.map { it.w.attempt })
        assertEquals(
            listOf(15_000L, 30_000L, 60_000L, 120_000L, 300_000L, 300_000L, 300_000L, 300_000L),
            backoffs.map { it.delayMs },
        )
        val f = h.stateOf<SyncState.Failed>()
        assertEquals(SyncError.NETWORK, f.error)
        assertEquals(SyncSummary(uploaded = 2), f.summary)
        assertTrue(backoffs.all { it.w.summary == SyncSummary(uploaded = 2) })
        assertEquals(1 + 9, h.uploader.calls.size)
        assertEquals(2, h.count(env, PENDING))
    }

    @Test
    fun counterResetsAfterProgress() = runTest {
        val h = harness(batch = 1)
        h.seed(env, 3)
        // calls: 1 fail, 2 fail, 3 accept f0, 4 fail, then accept
        h.uploader.handler = { _, files ->
            when (h.uploader.calls.size) {
                1, 2, 4 -> UploadAttempt.NetworkError("offline")
                else -> FakeUploader.acceptAll(files)
            }
        }
        val backoffs = recordBackoffs(h)
        h.engine.start(env)
        advanceUntilIdle()
        assertEquals(listOf(1, 2, 1), backoffs.map { it.w.attempt })
        assertEquals(listOf(15_000L, 30_000L, 15_000L), backoffs.map { it.delayMs })
        assertEquals(SyncSummary(uploaded = 3), h.stateOf<SyncState.Finished>().summary)
    }

    @Test
    fun autoRetryOff_failsAsBefore() = runTest {
        val h = harness()
        h.settings.autoRetry = false
        h.seed(env, 3)
        h.uploader.handler = { _, _ -> UploadAttempt.NetworkError("offline") }
        val backoffs = recordBackoffs(h)
        h.engine.start(env)
        advanceUntilIdle()
        assertEquals(SyncError.NETWORK, h.stateOf<SyncState.Failed>().error)
        assertTrue(backoffs.isEmpty())
        assertEquals(1, h.uploader.calls.size)
        assertEquals(3, h.count(env, PENDING))
    }

    @Test
    fun notRetried() = runTest {
        data class Case(val name: String, val error: SyncError, val setup: (SyncHarness) -> Unit)
        val cases = listOf(
            Case("503", SyncError.INBOX_FULL_OR_UNWRITABLE) { h ->
                h.uploader.handler = { _, _ -> UploadAttempt.HttpError(503, null, null) }
            },
            Case("507", SyncError.DISK_LOW) { h -> h.uploader.handler = { _, _ -> UploadAttempt.HttpError(507, null, null) } },
            Case("permission lost", SyncError.PERMISSION_LOST) { h ->
                h.uploader.handler = { _, _ -> h.access.granted.clear(); UploadAttempt.ReadFailed("denied") }
            },
            Case("backend too old", SyncError.SERVER) { h ->
                h.uploader.handler = { _, _ -> response(listOf(accepted(0, "f0.jpg", status = "ok"))) }
            },
            Case("three poison files", SyncError.SERVER) { h ->
                h.uploader.handler = { _, _ -> UploadAttempt.HttpError(500, null, null) }
            },
            Case("unexpected", SyncError.UNEXPECTED) { h -> h.uploader.handler = { _, _ -> UploadAttempt.Unexpected("bad") } },
            Case("4xx", SyncError.UNEXPECTED) { h -> h.uploader.handler = { _, _ -> UploadAttempt.HttpError(422, null, null) } },
            Case("no album", SyncError.NO_ALBUM) { h -> h.settings.albums.clear() },
        )
        for (c in cases) {
            val h = harness()
            h.seed(env, 3)
            c.setup(h)
            val backoffs = recordBackoffs(h)
            h.engine.start(env)
            advanceUntilIdle()
            assertEquals(c.name, c.error, h.stateOf<SyncState.Failed>().error)
            assertTrue(c.name, backoffs.isEmpty())
        }
    }

    @Test
    fun http5xx_retried_503_not() = runTest {
        val h = harness()
        h.seed(env, 2)
        failingFirst(h, 1) { UploadAttempt.HttpError(502, "bad gateway", null) }
        val backoffs = recordBackoffs(h)
        h.engine.start(env)
        advanceUntilIdle()
        assertEquals(1, backoffs.size)
        assertEquals(SyncSummary(uploaded = 2), h.stateOf<SyncState.Finished>().summary)

        val h2 = harness()
        h2.seed(env, 2)
        failingFirst(h2, 1) { UploadAttempt.HttpError(503, null, null) }
        val backoffs2 = recordBackoffs(h2)
        h2.engine.start(env)
        advanceUntilIdle()
        assertTrue(backoffs2.isEmpty())
        assertEquals(SyncError.INBOX_FULL_OR_UNWRITABLE, h2.stateOf<SyncState.Failed>().error)
        assertEquals(1, h2.uploader.calls.size)
    }

    @Test
    fun fileReadError_andRateLimited_areRetried() = runTest {
        val h = harness()
        h.seed(env, 2)
        failingFirst(h, 1) { UploadAttempt.ReadFailed("provider hiccup") }
        val backoffs = recordBackoffs(h)
        h.engine.start(env)
        advanceUntilIdle()
        assertEquals(1, backoffs.size)
        h.stateOf<SyncState.Finished>()

        // The precheck's unreadable file (grant intact) is FILE_READ_ERROR too.
        val h2 = harness()
        h2.seed(env, 2)
        h2.source.failing["f0"] = java.io.IOException("placeholder")
        val backoffs2 = recordBackoffs(h2)
        h2.engine.start(env)
        advanceTimeBy(1_000); runCurrent()
        assertEquals(1, backoffs2.size)
        h2.source.failing.clear()
        advanceUntilIdle()
        assertEquals(SyncSummary(uploaded = 2), h2.stateOf<SyncState.Finished>().summary)

        // 11 answers of 429 in a row: Failed(RATE_LIMITED) after its own 10 waits, then a retry.
        val h3 = harness()
        h3.seed(env, 2)
        failingFirst(h3, 11) { UploadAttempt.HttpError(429, null, 1) }
        val backoffs3 = recordBackoffs(h3)
        h3.engine.start(env)
        advanceUntilIdle()
        assertEquals(1, backoffs3.size)
        assertEquals(SyncSummary(uploaded = 2), h3.stateOf<SyncState.Finished>().summary)
    }

    @Test
    fun stop_duringBackoff_endsStopped_noMoreUploads() = runTest {
        val h = harness()
        h.seed(env, 3)
        h.uploader.handler = { _, _ -> UploadAttempt.NetworkError("offline") }
        h.engine.start(env)
        runCurrent()
        val w = h.stateOf<SyncState.Waiting>()
        assertEquals(WaitReason.RETRY_BACKOFF, w.reason)
        assertEquals(1, w.attempt)
        h.engine.stop(env)
        runCurrent()
        h.stateOf<SyncState.Stopped>()
        advanceUntilIdle()
        h.stateOf<SyncState.Stopped>()
        assertEquals(1, h.uploader.calls.size)
        assertEquals(3, h.count(env, PENDING))
    }

    @Test
    fun summaryKeepsAddingUpAcrossAttempts() = runTest {
        val h = harness()
        h.seed(env, 100)
        h.uploader.handler = { _, files ->
            if (h.uploader.calls.size == 2) UploadAttempt.NetworkError("dropped") else FakeUploader.acceptAll(files)
        }
        val backoffs = recordBackoffs(h)
        h.engine.start(env)
        advanceUntilIdle()
        assertEquals(listOf(SyncSummary(uploaded = 50)), backoffs.map { it.w.summary })
        assertEquals(SyncSummary(uploaded = 100), h.stateOf<SyncState.Finished>().summary)
        assertEquals(100, h.count(env, UPLOADED))
    }

    @Test
    fun settingChangeAppliesMidRun() = runTest {
        val h = harness()
        h.seed(env, 3)
        h.uploader.handler = { _, _ -> UploadAttempt.NetworkError("offline") }
        val backoffs = recordBackoffs(h)
        h.engine.start(env)
        runCurrent()
        assertEquals(1, backoffs.size)
        h.settings.autoRetry = false // switched off during the wait
        advanceUntilIdle()
        // The retry already scheduled still runs once; its failure ends the run.
        assertEquals(1, backoffs.size)
        assertEquals(2, h.uploader.calls.size)
        assertEquals(SyncError.NETWORK, h.stateOf<SyncState.Failed>().error)
        assertEquals(3, h.count(env, PENDING))
    }

    @Test
    fun poisonStreak_survivesARetry() = runTest {
        // A broken server: 500 for every batch; files sent alone get 500, 500, 502, 500, 500, 502, ...
        val h = harness()
        h.seed(env, 10)
        var single = 0
        h.uploader.handler = { _, files ->
            if (files.size > 1) UploadAttempt.HttpError(500, null, null)
            else if (++single % 3 == 0) UploadAttempt.HttpError(502, "bad gateway", null)
            else UploadAttempt.HttpError(500, null, null)
        }
        val backoffs = recordBackoffs(h)
        h.engine.start(env)
        advanceUntilIdle()
        // f0, f1 rejected, 502 (retry), then f2 is the third poison file in a row: the server is broken.
        val f = h.stateOf<SyncState.Failed>()
        assertEquals(SyncError.SERVER, f.error)
        assertEquals(1, backoffs.size)
        assertEquals(SyncSummary(rejected = 3), f.summary)
        assertEquals(7, h.count(env, PENDING))
    }

    @Test
    fun rejectionsAlone_doNotResetTheRetryCounter() = runTest {
        // Every attempt gets one file rejected by the server, then a network error: rejections are no proof of recovery.
        val h = harness(batch = 1)
        h.seed(env, 30)
        h.uploader.handler = { _, files ->
            if (h.uploader.calls.size % 2 == 1) {
                response(emptyList(), listOf(com.memebrowser.app.data.model.FailedFile(files[0].displayName, "bad", 0)))
            } else UploadAttempt.NetworkError("offline")
        }
        val backoffs = recordBackoffs(h)
        h.engine.start(env)
        advanceUntilIdle()
        assertEquals((1..8).toList(), backoffs.map { it.w.attempt })
        val f = h.stateOf<SyncState.Failed>()
        assertEquals(SyncError.NETWORK, f.error)
        assertEquals(SyncSummary(rejected = 9), f.summary)
        assertEquals(21, h.count(env, PENDING))
    }

    @Test
    fun transientFailureInsideTheIsolatePass_isRetried_filesNotLost() = runTest {
        for (failure in listOf<UploadAttempt>(UploadAttempt.HttpError(502, "bad gateway", null), UploadAttempt.NetworkError("dropped"))) {
            val h = harness()
            h.seed(env, 3)
            // call 1: batch -> 500; call 2: f0 alone -> accepted; call 3: f1 alone -> transient failure; then accept all.
            h.uploader.handler = { _, files ->
                when (h.uploader.calls.size) {
                    1 -> UploadAttempt.HttpError(500, null, null)
                    3 -> failure
                    else -> FakeUploader.acceptAll(files)
                }
            }
            val backoffs = recordBackoffs(h)
            h.engine.start(env)
            advanceUntilIdle()
            assertEquals("$failure", 1, backoffs.size)
            assertEquals("$failure", SyncSummary(uploaded = 3), h.stateOf<SyncState.Finished>().summary)
            assertEquals("$failure", 3, h.count(env, UPLOADED))
            assertEquals("$failure", listOf("f1", "f2"), h.uploader.calls[3].second)
        }
    }

    @Test
    fun twoEnvironments_haveIndependentCounters() = runTest {
        val other = "builtin-metal"
        val h = harness()
        h.seed(env, 2)
        h.seed(other, 2, prefix = "m")
        var otherCalls = 0
        h.uploader.handler = { e, files ->
            if (e == env) UploadAttempt.NetworkError("offline")
            else if (++otherCalls <= 2) UploadAttempt.NetworkError("offline") else FakeUploader.acceptAll(files)
        }
        val otherBackoffs = mutableListOf<Int>()
        backgroundScope.launch(UnconfinedTestDispatcher(testScheduler)) {
            h.engine.state(other).collect {
                if (it is SyncState.Waiting && it.reason == WaitReason.RETRY_BACKOFF) otherBackoffs += it.attempt
            }
        }
        h.engine.start(env)
        advanceTimeBy(100_000); runCurrent() // env is already past attempt 3 when other starts
        h.engine.start(other)
        advanceUntilIdle()
        assertEquals(listOf(1, 2), otherBackoffs)
        h.engine.state(other).value.let { assertTrue("$it", it is SyncState.Finished) }
        assertEquals(SyncError.NETWORK, h.stateOf<SyncState.Failed>().error)
    }
}
