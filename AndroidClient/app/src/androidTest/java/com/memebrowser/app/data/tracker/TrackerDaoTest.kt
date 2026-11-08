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
}
