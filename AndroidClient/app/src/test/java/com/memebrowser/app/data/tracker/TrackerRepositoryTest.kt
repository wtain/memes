package com.memebrowser.app.data.tracker

import androidx.datastore.preferences.core.PreferenceDataStoreFactory
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.yield
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
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
        r.syncScan(env, album, emptyList(), allowEmpty = true, scanId = 2)
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

    private fun bigListing() = (0 until 1000).map { file("id%04d".format(it)) }

    @Test
    fun `overlapping scans do not flag real files absent`() = runTest {
        val r = repo()
        dao.beforeChunk = { yield() }
        val files = bigListing()
        val a = launch { r.syncScan(env, album, files, scanId = 1) }
        val b = launch { r.syncScan(env, album, files, scanId = 2) }
        a.join(); b.join()
        assertEquals(TrackerCounts(0, 1000, 0, 1000), r.counts())
    }

    @Test
    fun `a reset cannot be undone by a scan that is already running`() = runTest {
        val r = repo()
        dao.beforeChunk = { yield() }
        val scan = launch { r.syncScan(env, album, bigListing(), scanId = 1) }
        val reset = launch { r.resetEnvironment(env) }
        scan.join(); reset.join()
        assertEquals(TrackerCounts.EMPTY, r.counts())
    }

    @Test
    fun `scan ids increase even when the clock does not`() = runTest {
        val r = repo()
        r.syncScan(env, album, listOf(file("a")))
        r.syncScan(env, album, listOf(file("b")))
        r.syncScan(env, album, listOf(file("b")))
        // a was absent from the second scan, whatever the millisecond
        assertEquals(TrackerCounts(0, 1, 0, 1), r.counts())
    }

    @Test
    fun `an empty listing is refused unless the album is explicitly empty`() = runTest {
        val r = repo()
        r.syncScan(env, album, listOf(file("a")), scanId = 1)
        assertThrows(IllegalArgumentException::class.java) { runBlocking { r.syncScan(env, album, emptyList()) } }
        assertEquals(TrackerCounts(0, 1, 0, 1), r.counts())
    }

    @Test
    fun `a renamed file is a new row beside the old one`() = runTest {
        // SAF document ids are path based, so a rename or move changes the documentId.
        val r = repo()
        r.syncScan(env, album, listOf(file("DCIM/old")), scanId = 1)
        r.markUploaded(env, album, listOf("DCIM/old"), at = 5)
        r.syncScan(env, album, listOf(file("DCIM/new")), scanId = 2)
        assertEquals(listOf("DCIM/new"), r.nextPending(env, 10).map { it.documentId })
        // X (1, old row, absent) is not bounded by Y (1, new row, pending): X can exceed Y after renames.
        assertEquals(TrackerCounts(uploaded = 1, supported = 1, rejected = 0, pending = 1), r.counts())
        r.markUploaded(env, album, listOf("DCIM/new"), at = 6)
        assertEquals(TrackerCounts(uploaded = 2, supported = 1, rejected = 0, pending = 0), r.counts())
    }

    @Test
    fun `markRejected cannot demote an uploaded file`() = runTest {
        val r = repo()
        r.syncScan(env, album, listOf(file("a")), scanId = 1)
        r.markUploaded(env, album, listOf("a"), at = 5)
        r.markRejected(env, album, "a", "late answer", at = 9)
        assertEquals(TrackerCounts(1, 1, 0, 0), r.counts())
        assertEquals(TrackedFileState.UPLOADED, dao.all.single().state)
    }

    @Test
    fun `a rejected row with no reason still counts in Y`() = runTest {
        val r = repo()
        r.syncScan(env, album, listOf(file("a")), scanId = 1)
        dao.upsertAll(dao.all.map { it.copy(state = TrackedFileState.REJECTED, rejectionReason = null) })
        assertEquals(TrackerCounts(0, 1, 1, 0), r.counts())
    }

    @Test
    fun `changeAlbum wipes the environment then stores the new album`() = runTest {
        val r = repo()
        r.setAlbum(env, "content://old", "Old")
        r.syncScan(env, "content://old", listOf(file("a")), scanId = 1)
        r.syncScan(other, album, listOf(file("a")), scanId = 1)
        r.changeAlbum(env, "content://new", "New")
        assertEquals(TrackerCounts.EMPTY, r.counts(env))
        assertEquals(1, r.counts(other).pending)
        assertEquals(AlbumSelection("content://new", "New"), r.album(env).first())
    }

    @Test
    fun `clearAlbum wipes the environment and removes the album`() = runTest {
        val r = repo()
        r.setAlbum(env, "content://old", "Old")
        r.syncScan(env, "content://old", listOf(file("a")), scanId = 1)
        r.clearAlbum(env)
        assertEquals(TrackerCounts.EMPTY, r.counts(env))
        assertEquals(null, r.album(env).first())
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

    // ---- A4: retry of server errors -------------------------------------------------------------------------

    private suspend fun TrackerRepository.seedRejected() {
        syncScan(env, album, listOf(file("a"), file("b"), file("c"), file("d"), file("e", mime = "image/heic")), scanId = 1)
        markRejected(env, album, "a", RejectionReasons.SERVER_ERROR)
        markRejected(env, album, "b", RejectionReasons.SERVER_ERROR)
        markRejected(env, album, "c", "File too large: more than 20")
        markUploaded(env, album, listOf("d"))
        // "e" is locally rejected as unsupported_type by the scan.
    }

    @Test
    fun `retryServerErrors resets only server_error rows to pending`() = runTest {
        val r = repo()
        r.seedRejected()
        assertEquals(2, r.retryServerErrors(env))
        val rows = dao.all.associateBy { it.documentId }
        assertEquals(TrackedFileState.PENDING, rows.getValue("a").state)
        assertEquals(null, rows.getValue("a").rejectionReason)
        assertEquals(TrackedFileState.PENDING, rows.getValue("b").state)
        assertEquals(TrackedFileState.REJECTED, rows.getValue("c").state)
        assertEquals("File too large: more than 20", rows.getValue("c").rejectionReason)
        assertEquals(TrackedFileState.UPLOADED, rows.getValue("d").state)
        assertEquals(TrackedFileState.REJECTED, rows.getValue("e").state)
        assertEquals(SupportedImages.REASON_UNSUPPORTED_TYPE, rows.getValue("e").rejectionReason)
        assertEquals(TrackerCounts(uploaded = 1, supported = 4, rejected = 2, pending = 2), r.counts())
        assertEquals(listOf("a", "b"), r.nextPending(env, 50).map { it.documentId }.sorted())
    }

    @Test
    fun `retryServerErrors does nothing when there are none and never touches another environment`() = runTest {
        val r = repo()
        r.seedRejected()
        r.syncScan(other, album, listOf(file("a")), scanId = 1)
        r.markRejected(other, album, "a", RejectionReasons.SERVER_ERROR)
        assertEquals(2, r.retryServerErrors(env))
        assertEquals(0, r.retryServerErrors(env))
        assertEquals(TrackedFileState.REJECTED, dao.all.first { it.environmentId == other }.state)
    }

    @Test
    fun `retryServerError resets one file and refuses other reasons`() = runTest {
        val r = repo()
        r.seedRejected()
        assertTrue(r.retryServerError(env, album, "a"))
        assertFalse(r.retryServerError(env, album, "a")) // no longer rejected
        assertFalse(r.retryServerError(env, album, "c")) // another reason
        assertFalse(r.retryServerError(env, album, "d")) // uploaded
        assertFalse(r.retryServerError(env, "content://other", "b")) // wrong album
        val rows = dao.all.associateBy { it.documentId }
        assertEquals(TrackedFileState.PENDING, rows.getValue("a").state)
        assertEquals(TrackedFileState.REJECTED, rows.getValue("b").state)
    }
}
