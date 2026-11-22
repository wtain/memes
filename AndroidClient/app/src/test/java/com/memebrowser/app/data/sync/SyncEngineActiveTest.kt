package com.memebrowser.app.data.sync

import com.memebrowser.app.data.repository.UploadAttempt
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

@OptIn(ExperimentalCoroutinesApi::class)
class SyncEngineActiveTest {

    @get:Rule
    val tmp = TemporaryFolder()

    private val a = "builtin-general"
    private val b = "builtin-metal"

    private fun TestScope.harness() = SyncHarness(testScheduler, tmp.root)

    @Test
    fun activeEnvironments_tracksStartAndEnd() = runTest {
        val h = harness()
        h.seed(a, 2)
        assertEquals(emptySet<String>(), h.engine.activeEnvironments.value)
        h.engine.start(a)
        assertEquals(setOf(a), h.engine.activeEnvironments.value)
        advanceUntilIdle()
        assertTrue(h.engine.state(a).value is SyncState.Finished)
        assertEquals(emptySet<String>(), h.engine.activeEnvironments.value)
    }

    @Test
    fun activeEnvironments_emptyAfterFailure() = runTest {
        val h = harness()
        h.seed(a, 1)
        h.uploader.handler = { _, _ -> UploadAttempt.NetworkError("down") }
        h.engine.start(a)
        advanceUntilIdle()
        assertTrue(h.engine.state(a).value is SyncState.Failed)
        assertEquals(emptySet<String>(), h.engine.activeEnvironments.value)
    }

    @Test
    fun activeEnvironments_emptyAfterStop() = runTest {
        val h = harness()
        h.seed(a, 1)
        h.uploader.handler = { _, _ -> kotlinx.coroutines.awaitCancellation() }
        h.engine.start(a)
        runCurrent()
        assertEquals(setOf(a), h.engine.activeEnvironments.value)
        h.engine.stop(a)
        assertEquals(setOf(a), h.engine.activeEnvironments.value) // Stopping is still active
        advanceUntilIdle()
        assertTrue(h.engine.state(a).value is SyncState.Stopped)
        assertEquals(emptySet<String>(), h.engine.activeEnvironments.value)
    }

    @Test
    fun twoEnvironments_areIndependent() = runTest {
        val h = harness()
        h.seed(a, 1)
        h.seed(b, 1)
        h.uploader.handler = { env, files ->
            if (env == b) kotlinx.coroutines.awaitCancellation() else FakeUploader.acceptAll(files)
        }
        h.engine.start(a)
        h.engine.start(b)
        assertEquals(setOf(a, b), h.engine.activeEnvironments.value)
        advanceUntilIdle()
        assertEquals(setOf(b), h.engine.activeEnvironments.value)
        h.engine.stop(b)
        advanceUntilIdle()
        assertEquals(emptySet<String>(), h.engine.activeEnvironments.value)
    }

    @Test
    fun start_callsTheStarterOnce_notWhenAlreadyRunning() = runTest {
        val h = harness()
        h.seed(a, 1)
        h.uploader.handler = { _, _ -> kotlinx.coroutines.awaitCancellation() }
        assertTrue(h.engine.start(a))
        assertEquals(1, h.starter.calls)
        assertFalse(h.engine.start(a))
        assertEquals(1, h.starter.calls)
        h.engine.stop(a)
        advanceUntilIdle()
    }
}
