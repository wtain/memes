package com.memebrowser.app.data.album

import kotlinx.coroutines.CompletableDeferred

class FakeAlbumAccess : AlbumAccess {
    val granted = mutableSetOf<String>()
    val released = mutableListOf<String>()
    var persistFails = false
    val persisted = mutableListOf<String>()

    /** Runs right after a successful persist (to cancel the caller at that exact point). */
    var afterPersist: (() -> Unit)? = null

    override fun persist(treeUri: String): Boolean {
        if (persistFails) return false
        granted += treeUri
        persisted += treeUri
        afterPersist?.invoke()
        return true
    }

    override fun isGranted(treeUri: String) = treeUri in granted

    override fun release(treeUri: String) {
        granted -= treeUri
        released += treeUri
    }
}

class FakeAlbumScanner : AlbumScanner {
    val calls = mutableListOf<String>()
    var result: (String) -> AlbumScanResult = { AlbumScanResult.Complete(emptyList()) }

    /** When set, [scan] suspends until it completes (to hold a scan in flight). */
    var hold: ((String) -> CompletableDeferred<Unit>?)? = null

    override suspend fun scan(treeUri: String): AlbumScanResult {
        calls += treeUri
        hold?.invoke(treeUri)?.await()
        return result(treeUri)
    }
}

/** Records, at the moment of the call, whatever [probe] reports (for example how many tracker rows still exist). */
class RecordingSyncGate(private val probe: () -> Int = { 0 }) : SyncGate {
    val cancelled = mutableListOf<String>()
    val probeAtCancel = mutableListOf<Int>()

    override suspend fun cancelAndAwait(environmentId: String) {
        cancelled += environmentId
        probeAtCancel += probe()
    }
}
