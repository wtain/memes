package com.memebrowser.app.data.album

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class SafAlbumScannerTest {
    private val tree = "content://x/tree/primary%3ADCIM%2FCamera"
    private val access = FakeAlbumAccess().apply { granted += tree }

    private fun scanner(a: AlbumAccess = access, source: AlbumRowSource) =
        SafAlbumScanner(source, a, Dispatchers.Unconfined)

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
}
