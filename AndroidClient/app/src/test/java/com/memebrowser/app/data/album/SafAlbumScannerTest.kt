package com.memebrowser.app.data.album

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.runBlocking
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class SafAlbumScannerTest {
    private val tree = "content://x/tree/primary%3ADCIM%2FCamera"
    private val access = FakeAlbumAccess().apply { granted += tree }

    private fun scanner(a: AlbumAccess = access, source: (String) -> RawListing?) =
        SafAlbumScanner({ uri, _ -> source(uri) }, a, Dispatchers.Unconfined)

    private fun row(id: String?, name: String? = "n", mime: String? = "image/jpeg", size: Long? = 5, mod: Long? = 7) =
        AlbumRow(id, name, mime, size, mod)

    @Test
    fun `complete listing maps rows and skips directories`() = runTest {
        val rows = listOf(
            row("d1", mime = "vnd.android.document/directory"),
            row("a", name = "a.jpg"),
            row("b", name = "b.heic", mime = "image/heic"),
        )
        val r = scanner { RawListing(rows, loading = false) }.scan(tree)
        val files = (r as AlbumScanResult.Complete).files
        assertEquals(listOf("a", "b"), files.map { it.documentId })
        assertEquals("a.jpg", files[0].displayName)
        assertEquals(5L, files[0].size)
        assertEquals(7L, files[0].lastModified)
    }

    @Test
    fun `null columns are tolerated`() {
        val files = SafAlbumScanner.map(
            listOf(
                row(null),
                row("", name = "empty id"),
                row("primary:DCIM/Camera/x.jpg", name = null, mime = null, size = null, mod = null),
                row("neg", size = -1, mod = -1),
            )
        )
        assertEquals(2, files.size)
        assertEquals("x.jpg", files[0].displayName)
        assertEquals("", files[0].mimeType)
        assertEquals(0L, files[0].size)
        assertEquals(0L, files[0].lastModified)
        assertEquals(0L, files[1].size)
    }

    @Test
    fun `repeated document id keeps the first row`() {
        val files = SafAlbumScanner.map(listOf(row("a", name = "first"), row("a", name = "second")))
        assertEquals(listOf("first"), files.map { it.displayName })
    }

    @Test
    fun `empty listing is complete`() = runTest {
        val r = scanner { RawListing(emptyList(), loading = false) }.scan(tree)
        assertEquals(AlbumScanResult.Complete(emptyList()), r)
    }

    @Test
    fun `loading provider is incomplete even with rows`() = runTest {
        val r = scanner { RawListing(listOf(row("a")), loading = true) }.scan(tree)
        assertTrue(r is AlbumScanResult.Incomplete)
    }

    @Test
    fun `null cursor is incomplete`() = runTest {
        assertTrue(scanner { null }.scan(tree) is AlbumScanResult.Incomplete)
    }

    @Test
    fun `other exceptions are incomplete, never thrown`() = runTest {
        assertTrue(scanner { throw IllegalStateException("boom") }.scan(tree) is AlbumScanResult.Incomplete)
    }

    @Test
    fun `security exception is permission lost`() = runTest {
        assertEquals(AlbumScanResult.PermissionLost, scanner { throw SecurityException() }.scan(tree))
    }

    @Test
    fun `missing persisted grant is permission lost without querying`() = runTest {
        var queried = false
        val r = scanner(FakeAlbumAccess()) { queried = true; RawListing(emptyList(), false) }.scan(tree)
        assertEquals(AlbumScanResult.PermissionLost, r)
        assertTrue(!queried)
    }

    @Test
    fun `empty listing whose root no longer resolves is incomplete, not an empty album`() = runTest {
        val r = scanner { RawListing(emptyList(), loading = false, rootResolves = false) }.scan(tree)
        assertEquals(AlbumScanResult.Incomplete("album unavailable"), r)
    }

    @Test
    fun `root flag is ignored when there are rows`() = runTest {
        val r = scanner { RawListing(listOf(row("a")), loading = false, rootResolves = false) }.scan(tree)
        assertTrue(r is AlbumScanResult.Complete)
    }

    @Test
    fun `cancelling the scan aborts a hung query and returns at once`() = runBlocking {
        val entered = CountDownLatch(1)
        val aborted = CountDownLatch(1)
        val source = AlbumRowSource { _, cancel ->
            cancel.onCancel { aborted.countDown() }
            entered.countDown()
            aborted.await(10, TimeUnit.SECONDS) // a blocking provider call that only the abort ends
            null
        }
        val s = SafAlbumScanner(source, access, Dispatchers.IO)
        val job = async(Dispatchers.Default) { s.scan(tree) }
        assertTrue(entered.await(5, TimeUnit.SECONDS))
        job.cancel()
        assertTrue("the source was told to abort", aborted.await(5, TimeUnit.SECONDS))
        assertTrue(job.isCancelled)
    }

    @Test
    fun `a hung query times out as incomplete`() = runBlocking {
        val release = CountDownLatch(1)
        val aborted = CountDownLatch(1)
        val source = AlbumRowSource { _, cancel ->
            cancel.onCancel { aborted.countDown(); release.countDown() }
            release.await(10, TimeUnit.SECONDS)
            null
        }
        val r = SafAlbumScanner(source, access, Dispatchers.IO, timeoutMillis = 100).scan(tree)
        assertEquals(AlbumScanResult.Incomplete("listing timed out"), r)
        assertTrue(aborted.await(5, TimeUnit.SECONDS))
    }

    private class FakeReader(
        private val columns: Map<String, Int>,
        private val data: List<Map<Int, Any?>>,
    ) : RowReader {
        private var pos = -1
        override fun moveToNext() = ++pos < data.size
        override fun columnIndex(name: String) = columns[name] ?: -1
        override fun isNull(column: Int) = data[pos][column] == null
        override fun getString(column: Int) = data[pos][column] as String?
        override fun getLong(column: Int) = data[pos][column] as Long
    }

    @Test
    fun `cursor reading maps columns and tolerates missing and null columns`() {
        val cols = mapOf("document_id" to 0, "_display_name" to 1, "mime_type" to 2, "_size" to 3, "last_modified" to 4)
        val rows = readRows(
            FakeReader(
                cols,
                listOf(
                    mapOf(0 to "a", 1 to "a.jpg", 2 to "image/jpeg", 3 to 10L, 4 to 20L),
                    mapOf(0 to "b", 1 to null, 2 to null, 3 to null, 4 to null),
                ),
            )
        )
        assertEquals(AlbumRow("a", "a.jpg", "image/jpeg", 10L, 20L), rows[0])
        assertEquals(AlbumRow("b", null, null, null, null), rows[1])
        val partial = readRows(FakeReader(mapOf("document_id" to 0), listOf(mapOf(0 to "x"))))
        assertEquals(listOf(AlbumRow("x", null, null, null, null)), partial)
        assertTrue(readRows(FakeReader(emptyMap(), emptyList())).isEmpty())
    }
}
