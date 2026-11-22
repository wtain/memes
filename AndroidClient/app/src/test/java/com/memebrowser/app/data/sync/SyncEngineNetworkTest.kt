package com.memebrowser.app.data.sync

import com.memebrowser.app.data.repository.UploadAttempt
import com.memebrowser.app.data.tracker.TrackedFileState.PENDING
import com.memebrowser.app.data.tracker.TrackedFileState.UPLOADED
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.TestScope
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
class SyncEngineNetworkTest {

    @get:Rule
    val tmp = TemporaryFolder()

    private val env = "builtin-general"

    private fun TestScope.harness() = SyncHarness(testScheduler, tmp.root)

    private inline fun <reified T : SyncState> SyncHarness.stateOf(): T {
        val s = engine.state(env).value
        assertTrue("state was $s", s is T)
        return s as T
    }

    private fun SyncHarness.assertWaiting(reason: WaitReason) {
        val w = stateOf<SyncState.Waiting>()
        assertEquals(reason, w.reason)
        assertEquals(0L, w.resumeAt)
        assertEquals(0, w.attempt)
    }

    @Test
    fun wifiOnly_onMobile_waitsForWifiThenUploads() = runTest {
        val h = harness()
        h.settings.wifiOnly = true
        h.network.set(NetworkStatus.NEEDS_WIFI)
        h.seed(env, 3)
        h.engine.start(env)
        advanceTimeBy(60_000); runCurrent()
        h.assertWaiting(WaitReason.NEEDS_WIFI)
        assertTrue(h.uploader.calls.isEmpty())

        h.network.set(NetworkStatus.OK)
        advanceUntilIdle()
        assertEquals(SyncSummary(uploaded = 3), h.stateOf<SyncState.Finished>().summary)
        assertEquals(3, h.count(env, UPLOADED))
    }

    @Test
    fun noNetwork_autoRetryOn_waitsThenResumes() = runTest {
        val h = harness()
        h.settings.autoRetry = true
        h.network.set(NetworkStatus.NONE)
        h.seed(env, 3)
        h.engine.start(env)
        advanceTimeBy(60_000); runCurrent()
        h.assertWaiting(WaitReason.NO_NETWORK)
        assertTrue(h.uploader.calls.isEmpty())

        h.network.set(NetworkStatus.OK)
        advanceUntilIdle()
        assertEquals(SyncSummary(uploaded = 3), h.stateOf<SyncState.Finished>().summary)
    }

    @Test
    fun noNetwork_autoRetryOff_isNotWaitedFor() = runTest {
        val h = harness()
        h.settings.autoRetry = false
        h.network.set(NetworkStatus.NONE)
        h.uploader.handler = { _, _ -> UploadAttempt.NetworkError("offline") }
        h.seed(env, 3)
        h.engine.start(env)
        advanceUntilIdle()
        assertEquals(SyncError.NETWORK, h.stateOf<SyncState.Failed>().error)
        assertEquals(1, h.uploader.calls.size)
        assertEquals(3, h.count(env, PENDING))
    }

    @Test
    fun wifiOnly_offWhileWaiting_endsTheWait() = runTest {
        val h = harness()
        h.settings.wifiOnly = true
        h.network.set(NetworkStatus.NEEDS_WIFI)
        h.seed(env, 3)
        h.engine.start(env)
        advanceTimeBy(30_000); runCurrent()
        h.assertWaiting(WaitReason.NEEDS_WIFI)

        h.settings.wifiOnly = false
        h.network.set(NetworkStatus.NEEDS_WIFI) // a network event: the gate is re-evaluated with wifiOnly = false
        advanceUntilIdle()
        assertEquals(SyncSummary(uploaded = 3), h.stateOf<SyncState.Finished>().summary)
    }

    @Test
    fun wifiOnly_offWhileWaiting_endsTheWaitWithinFiveSecondsWithoutANetworkEvent() = runTest {
        val h = harness()
        h.settings.wifiOnly = true
        h.network.set(NetworkStatus.NEEDS_WIFI)
        h.seed(env, 3)
        h.engine.start(env)
        advanceTimeBy(30_000); runCurrent()
        h.assertWaiting(WaitReason.NEEDS_WIFI)

        h.settings.wifiOnly = false // no network event at all
        advanceTimeBy(5_001); runCurrent()
        assertEquals(SyncSummary(uploaded = 3), h.stateOf<SyncState.Finished>().summary)
    }

    @Test
    fun wifiOnly_onWhileOnMobile_startsAWait() = runTest {
        val h = harness()
        h.settings.batchSize = 1
        h.network.set(NetworkStatus.NEEDS_WIFI) // on a metered network, Wi-Fi only off: uploads go
        h.uploader.handler = { _, files -> h.settings.wifiOnly = true; FakeUploader.acceptAll(files) }
        h.seed(env, 3)
        h.engine.start(env)
        advanceTimeBy(30_000); runCurrent()
        assertEquals(1, h.uploader.calls.size)
        h.assertWaiting(WaitReason.NEEDS_WIFI)
        h.engine.stop(env); runCurrent()
        h.stateOf<SyncState.Stopped>()
        assertEquals(1, h.uploader.calls.size)
    }

    @Test
    fun isolatePass_respectsWifiOnly() = runTest {
        val h = harness()
        h.network.set(NetworkStatus.NEEDS_WIFI) // metered network, Wi-Fi only off: uploads go
        h.uploader.handler = { _, files ->
            if (files.size > 1) {
                UploadAttempt.HttpError(500, null, null)
            } else {
                h.settings.wifiOnly = true // switched on during the isolate pass
                FakeUploader.acceptAll(files)
            }
        }
        h.seed(env, 3)
        h.engine.start(env)
        advanceTimeBy(60_000); runCurrent()
        assertEquals(listOf(listOf("f0", "f1", "f2"), listOf("f0")), h.uploader.calls.map { it.second })
        h.assertWaiting(WaitReason.NEEDS_WIFI)

        h.network.set(NetworkStatus.OK)
        advanceUntilIdle()
        assertEquals(
            listOf(listOf("f0", "f1", "f2"), listOf("f0"), listOf("f1"), listOf("f2")),
            h.uploader.calls.map { it.second },
        )
        assertEquals(SyncSummary(uploaded = 3), h.stateOf<SyncState.Finished>().summary)
    }

    @Test
    fun stop_whileWaitingForNetwork_endsStopped() = runTest {
        val h = harness()
        h.settings.autoRetry = true
        h.network.set(NetworkStatus.NONE)
        h.seed(env, 3)
        h.engine.start(env)
        advanceTimeBy(30_000); runCurrent()
        h.assertWaiting(WaitReason.NO_NETWORK)

        h.engine.stop(env)
        runCurrent()
        assertEquals(SyncSummary(), h.stateOf<SyncState.Stopped>().summary)
        h.network.set(NetworkStatus.OK)
        advanceUntilIdle()
        assertTrue(h.uploader.calls.isEmpty())
        assertEquals(3, h.count(env, PENDING))
    }

    @Test
    fun stop_whileWaitingForWifi_endsStopped() = runTest {
        val h = harness()
        h.settings.wifiOnly = true
        h.network.set(NetworkStatus.NEEDS_WIFI)
        h.seed(env, 3)
        h.engine.start(env)
        advanceTimeBy(30_000); runCurrent()
        h.engine.stop(env)
        runCurrent()
        h.stateOf<SyncState.Stopped>()
        h.network.set(NetworkStatus.OK)
        advanceUntilIdle()
        assertTrue(h.uploader.calls.isEmpty())
    }

    @Test
    fun pendingIsReReadAfterTheWait() = runTest {
        val h = harness()
        h.settings.wifiOnly = true
        h.network.set(NetworkStatus.NEEDS_WIFI)
        h.seed(env, 3)
        h.engine.start(env)
        advanceTimeBy(30_000); runCurrent()
        h.assertWaiting(WaitReason.NEEDS_WIFI)

        h.seed(env, 4) // a scan during the wait adds f3
        h.network.set(NetworkStatus.OK)
        advanceUntilIdle()
        assertEquals(listOf(listOf("f0", "f1", "f2", "f3")), h.uploader.calls.map { it.second })
        assertEquals(SyncSummary(uploaded = 4), h.stateOf<SyncState.Finished>().summary)
    }

    @Test
    fun waitingIsPublishedOnce_perReason() = runTest {
        val h = harness()
        h.settings.wifiOnly = true
        h.network.set(NetworkStatus.NEEDS_WIFI)
        h.seed(env, 1)
        val seen = h.recordStates(env, backgroundScope, testScheduler)
        h.engine.start(env)
        advanceTimeBy(60_000); runCurrent()
        h.network.set(NetworkStatus.NEEDS_WIFI); runCurrent() // an event that changes nothing
        assertEquals(1, seen.count { it is SyncState.Waiting })
        h.network.set(NetworkStatus.OK)
        advanceUntilIdle()
        h.stateOf<SyncState.Finished>()
    }
}
