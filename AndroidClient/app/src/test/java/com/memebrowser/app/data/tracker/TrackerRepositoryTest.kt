package com.memebrowser.app.data.tracker

import androidx.datastore.preferences.core.PreferenceDataStoreFactory
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

class TrackerRepositoryTest {

    @get:Rule
    val tmp = TemporaryFolder()

    private val dao = FakeTrackerDao()
    private val env = "builtin-general"
    private val other = "builtin-it"
    private val album = "content://tree/album"

    private fun repo(): TrackerRepository {
        val store = TrackerSettingsStore(PreferenceDataStoreFactory.create(produceFile = { File(tmp.root, "s.preferences_pb") }))
        return TrackerRepository(dao, store)
    }

    private fun file(id: String, mime: String = "image/jpeg", size: Long = 10, modified: Long = 1) =
        ScannedFile(id, "$id.jpg", mime, size, modified)

    private suspend fun TrackerRepository.counts(e: String = env) = observeCounts(e).first()

    @Test
    fun `scan classifies new files and counts them`() = runTest {
        val r = repo()
        r.syncScan(env, album, listOf(file("a"), file("b"), file("c", mime = "image/heic"), file("d", size = 30L * 1024 * 1024)), scanId = 1)
        // Y excludes the unsupported type only; R lists both locally rejected files.
        assertEquals(TrackerCounts(uploaded = 0, supported = 3, rejected = 2, pending = 2), r.counts())
    }

    @Test
    fun `uploaded file edited on the phone is not re-sent`() = runTest {
        val r = repo()
        r.syncScan(env, album, listOf(file("a")), scanId = 1)
        r.markUploaded(env, album, listOf("a"), at = 50)
        r.syncScan(env, album, listOf(file("a", size = 999, modified = 9)), scanId = 2)
        assertEquals(TrackerCounts(1, 1, 0, 0), r.counts())
        assertTrue(r.nextPending(env, 50).isEmpty())
    }

    @Test
    fun `uploaded file deleted from the album stays in X and leaves Y`() = runTest {
        val r = repo()
        r.syncScan(env, album, listOf(file("a"), file("b")), scanId = 1)
        r.markUploaded(env, album, listOf("a"), at = 50)
        r.syncScan(env, album, listOf(file("b")), scanId = 2)
        assertEquals(TrackerCounts(uploaded = 1, supported = 1, rejected = 0, pending = 1), r.counts())
    }

    @Test
    fun `pending file deleted from the album leaves Y and P and comes back when it reappears`() = runTest {
        val r = repo()
        r.syncScan(env, album, listOf(file("a")), scanId = 1)
        r.syncScan(env, album, emptyList(), scanId = 2)
        assertEquals(TrackerCounts.EMPTY, r.counts())
        r.syncScan(env, album, listOf(file("a")), scanId = 3)
        assertEquals(TrackerCounts(0, 1, 0, 1), r.counts())
    }

    @Test
    fun `nextPending is ordered by lastModified desc then documentId and respects the limit`() = runTest {
        val r = repo()
        r.syncScan(env, album, listOf(file("b", modified = 5), file("a", modified = 5), file("z", modified = 9), file("old", modified = 1)), scanId = 1)
        assertEquals(listOf("z", "a", "b"), r.nextPending(env, 3).map { it.documentId })
    }

    @Test
    fun `markUploaded is idempotent and keeps the first uploadedAt`() = runTest {
        val r = repo()
        r.syncScan(env, album, listOf(file("a")), scanId = 1)
        r.markUploaded(env, album, listOf("a"), at = 50)
        r.markUploaded(env, album, listOf("a"), at = 99)
        assertEquals(50L, dao.all.single().uploadedAt)
        assertEquals(1, r.counts().uploaded)
    }

    @Test
    fun `markRejected moves a pending file to the rejected list with its reason`() = runTest {
        val r = repo()
        r.syncScan(env, album, listOf(file("a")), scanId = 1)
        r.markRejected(env, album, "a", "bad content", at = 60)
        val rejected = r.rejectedFiles(env).first()
        assertEquals(listOf("a"), rejected.map { it.documentId })
        assertEquals("bad content", rejected.single().rejectionReason)
        assertEquals(TrackerCounts(0, 1, 1, 0), r.counts())
        // not retried: still rejected after another scan
        r.syncScan(env, album, listOf(file("a")), scanId = 2)
        assertTrue(r.nextPending(env, 50).isEmpty())
    }

    @Test
    fun `resetEnvironment wipes only that environment`() = runTest {
        val r = repo()
        r.syncScan(env, album, listOf(file("a")), scanId = 1)
        r.syncScan(other, album, listOf(file("a")), scanId = 1)
        r.resetEnvironment(env)
        assertEquals(TrackerCounts.EMPTY, r.counts(env))
        assertEquals(TrackerCounts(0, 1, 0, 1), r.counts(other))
    }

    @Test
    fun `scanning in one environment does not mark another environment missing`() = runTest {
        val r = repo()
        r.syncScan(other, album, listOf(file("a")), scanId = 1)
        r.syncScan(env, album, listOf(file("b")), scanId = 2)
        assertEquals(1, r.counts(other).pending)
    }

    @Test
    fun `scanning 5000 files uses chunked transactions and counts correctly`() = runTest {
        val r = repo()
        val files = (0 until 5000).map { file("id%05d".format(it), modified = it.toLong(), mime = if (it % 10 == 0) "image/heic" else "image/jpeg") }
        r.syncScan(env, album, files, scanId = 1)
        assertEquals(10, dao.transactionCount)
        assertEquals(TrackerCounts(0, 4500, 500, 4500), r.counts())
        r.markUploaded(env, album, r.nextPending(env, 50).map { it.documentId }, at = 5)
        assertEquals(TrackerCounts(50, 4500, 500, 4450), r.counts())
    }

    @Test
    fun `album and batch size round trip through the repository`() = runTest {
        val r = repo()
        assertEquals(null, r.album(env).first())
        r.setAlbum(env, "content://x", "Memes")
        assertEquals(AlbumSelection("content://x", "Memes"), r.album(env).first())
        assertEquals(50, r.batchSize().first())
        r.setBatchSize(7)
        assertEquals(7, r.batchSize().first())
    }
}
