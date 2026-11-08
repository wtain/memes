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
    val unavailable = mutableSetOf<String>()
    override fun open(treeUri: String, documentId: String): InputStream {
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

@OptIn(ExperimentalCoroutinesApi::class)
class SyncHarness(scheduler: TestCoroutineScheduler, tmp: File, batchSize: Int = 50) {
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
    val engine = SyncEngine(tracker, settings, access, uploader, source, scope, dispatcher) { scheduler.currentTime }

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
