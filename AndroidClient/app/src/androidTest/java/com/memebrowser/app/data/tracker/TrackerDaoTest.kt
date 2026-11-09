package com.memebrowser.app.data.tracker

import android.content.Context
import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test

/** Runs the real SQL against an in-memory Room database. Needs a device or emulator. */
class TrackerDaoTest {
    private lateinit var db: TrackerDatabase
    private lateinit var dao: TrackerDao
    private val env = "builtin-general"
    private val album = "content://tree/album"

    @Before
    fun setUp() {
        val context = ApplicationProvider.getApplicationContext<Context>()
        db = Room.inMemoryDatabaseBuilder(context, TrackerDatabase::class.java).build()
        dao = db.trackerDao()
    }

    @After
    fun tearDown() = db.close()

    private fun file(id: String, mime: String = "image/jpeg", size: Long = 10, modified: Long = 1) =
        ScannedFile(id, "$id.jpg", mime, size, modified)

    @Test
    fun countsOrderingAndD2Rules() = runBlocking {
        dao.applyScanChunk(
            env, album,
            listOf(file("a", modified = 5), file("b", modified = 9), file("c", mime = "image/heic"), file("d", size = 30L * 1024 * 1024)),
            scanId = 1, now = 1,
        )
        assertEquals(TrackerCounts(0, 3, 2, 2), dao.observeCounts(env).first())
        assertEquals(listOf("b", "a"), dao.nextPending(env, 10).map { it.documentId })

        dao.markUploadedAll(env, album, listOf("a"), at = 10)
        dao.markUploadedAll(env, album, listOf("a"), at = 20)
        assertEquals(10L, dao.findByIds(env, album, listOf("a")).single().uploadedAt)

        // edited file stays uploaded; deleted file leaves Y but stays in X
        dao.applyScanChunk(env, album, listOf(file("b", size = 99)), scanId = 2, now = 2)
        dao.markMissing(env, 2)
        assertEquals(TrackerCounts(1, 1, 0, 1), dao.observeCounts(env).first())

        dao.markRejected(env, album, "b", "bad content", at = 30)
        assertEquals(listOf("b"), dao.observeRejected(env).first().map { it.documentId })

        dao.deleteEnvironment(env)
        assertEquals(TrackerCounts.EMPTY, dao.observeCounts(env).first())
    }

    /** M1: a REJECTED row with a NULL reason must stay in Y (the SQL uses COALESCE), like FakeTrackerDao. */
    @Test
    fun rejectedWithNullReasonCountsInSupported() = runBlocking {
        dao.applyScanChunk(env, album, listOf(file("a")), scanId = 1, now = 1)
        dao.upsertAll(dao.findByIds(env, album, listOf("a")).map { it.copy(state = TrackedFileState.REJECTED, rejectionReason = null) })
        assertEquals(TrackerCounts(0, 1, 1, 0), dao.observeCounts(env).first())
    }

    /** M3: markRejected never demotes an UPLOADED row. */
    @Test
    fun markRejectedDoesNotDemoteUploaded() = runBlocking {
        dao.applyScanChunk(env, album, listOf(file("a")), scanId = 1, now = 1)
        dao.markUploadedAll(env, album, listOf("a"), at = 5)
        dao.markRejected(env, album, "a", "late", at = 9)
        assertEquals(TrackedFileState.UPLOADED, dao.findByIds(env, album, listOf("a")).single().state)
    }

    @Test
    fun maxScanIdIsNullForEmptyEnvironment() = runBlocking {
        assertEquals(null, dao.maxScanId(env))
        dao.applyScanChunk(env, album, listOf(file("a")), scanId = 7, now = 1)
        assertEquals(7L, dao.maxScanId(env))
    }
}
