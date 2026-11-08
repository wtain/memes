package com.memebrowser.app.data.sync

import androidx.datastore.preferences.core.PreferenceDataStoreFactory
import com.memebrowser.app.data.album.FakeAlbumAccess
import com.memebrowser.app.data.model.FailedFile
import com.memebrowser.app.data.model.UploadResponse
import com.memebrowser.app.data.model.UploadedFile
import com.memebrowser.app.data.repository.AlbumFileSource
import com.memebrowser.app.data.repository.BatchUploader
import com.memebrowser.app.data.repository.UploadAttempt
import com.memebrowser.app.data.repository.UploadFile
import com.memebrowser.app.data.tracker.AlbumSelection
import com.memebrowser.app.data.tracker.FakeTrackerDao
import com.memebrowser.app.data.tracker.ScannedFile
import com.memebrowser.app.data.tracker.TrackedFileState
import com.memebrowser.app.data.tracker.TrackerRepository
import com.memebrowser.app.data.tracker.TrackerSettingsStore
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import kotlinx.coroutines.yield
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestCoroutineScheduler
import java.io.ByteArrayInputStream
import java.io.File
import java.io.FileNotFoundException
import java.io.InputStream

const val TREE = "content://x/tree/primary%3ADCIM%2FCamera"

class FakeSyncSettings(var batchSize: Int = 50) : SyncSettingsSource {
    val albums = HashMap<String, AlbumSelection?>()
    override suspend fun album(environmentId: String) = albums[environmentId]
    override suspend fun batchSize() = batchSize
}

class FakeFileSource : AlbumFileSource {
    /** Documents that are gone (FileNotFoundException). */
    val unavailable = mutableSetOf<String>()

    /** Documents that fail with a non-FileNotFound exception. */
    val failing = HashMap<String, Exception>()
    var onOpen: ((String) -> Unit)? = null
    override fun open(treeUri: String, documentId: String): InputStream {
        onOpen?.invoke(documentId)
        failing[documentId]?.let { throw it }
        if (documentId in unavailable) throw FileNotFoundException(documentId)
        return ByteArrayInputStream(ByteArray(1))
    }
}

fun accepted(index: Int, name: String, status: String = "accepted") =
    UploadedFile(name, name, 1, "image/jpeg", status, index)

/** Scripted uploader: [handler] decides every call; the default accepts all files with their index. */
class FakeUploader : BatchUploader {
    val calls = mutableListOf<Pair<String, List<String>>>() // environment to document ids
    var handler: suspend (String, List<UploadFile>) -> UploadAttempt = { _, files -> acceptAll(files) }

    override suspend fun upload(environmentId: String, files: List<UploadFile>): UploadAttempt {
        calls += environmentId to files.map { it.documentId }
        return handler(environmentId, files)
    }

    companion object {
        fun acceptAll(files: List<UploadFile>, status: String = "accepted") = UploadAttempt.Response(
            UploadResponse(files.mapIndexed { i, f -> accepted(i, f.displayName, status) }, emptyList(), files.size, 0)
        )
    }
}

fun response(uploaded: List<UploadedFile>, failed: List<FailedFile> = emptyList()) =
    UploadAttempt.Response(UploadResponse(uploaded, failed, uploaded.size, failed.size))

/** Tracker writes that suspend (like Room does), with hooks, so cancellation can land inside the engine's apply step. */
internal class HookedOutcomes(private val inner: OutcomeStore) : OutcomeStore {
    var onMarkUploaded: (suspend () -> Unit)? = null
    override suspend fun markUploaded(environmentId: String, treeUri: String, documentIds: List<String>) {
        yield()
        onMarkUploaded?.invoke()
        inner.markUploaded(environmentId, treeUri, documentIds)
    }

    override suspend fun markRejected(environmentId: String, treeUri: String, documentId: String, reason: String) {
        yield()
        inner.markRejected(environmentId, treeUri, documentId, reason)
    }
}

@OptIn(ExperimentalCoroutinesApi::class)
internal class SyncHarness(scheduler: TestCoroutineScheduler, tmp: File, batchSize: Int = 50) {
    val dao = FakeTrackerDao()
    val tracker = TrackerRepository(
        dao,
        TrackerSettingsStore(PreferenceDataStoreFactory.create(produceFile = { File(tmp, "s.preferences_pb") })),
    )
    val settings = FakeSyncSettings(batchSize)
    val access = FakeAlbumAccess().also { it.granted += TREE }
    val uploader = FakeUploader()
    val source = FakeFileSource()
    val dispatcher = StandardTestDispatcher(scheduler)
    val scope = CoroutineScope(dispatcher + SupervisorJob())
    val outcomes = HookedOutcomes(TrackerOutcomeStore(tracker))
    val engine = SyncEngine(tracker, settings, access, uploader, source, scope, dispatcher, { scheduler.currentTime }, outcomes)

    /** Every state the engine published for [env], in order (collected eagerly under the test scheduler). */
    fun recordStates(env: String, into: kotlinx.coroutines.CoroutineScope, scheduler: TestCoroutineScheduler): MutableList<SyncState> {
        val seen = mutableListOf<SyncState>()
        into.launch(kotlinx.coroutines.test.UnconfinedTestDispatcher(scheduler)) { engine.state(env).collect { seen += it } }
        return seen
    }

    /** Adds [count] pending files `f0..` (newest first, so they are sent in this order) to [env], with the album stored. */
    suspend fun seed(env: String, count: Int, prefix: String = "f", from: Int = 0) {
        settings.albums[env] = AlbumSelection(TREE, "Camera")
        val files = (from until from + count).map {
            ScannedFile("$prefix$it", "$prefix$it.jpg", "image/jpeg", 10, 1_000_000L - it)
        }
        if (files.isNotEmpty()) tracker.syncScan(env, TREE, files, scanId = 1L + from)
    }

    fun rows(env: String) = dao.all.filter { it.environmentId == env }
    fun count(env: String, state: TrackedFileState) = rows(env).count { it.state == state }
    fun row(env: String, id: String) = rows(env).first { it.documentId == id }
}
