package com.memebrowser.app.data.album

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

private class FakeReader(private val cols: List<String>, private val data: List<List<Any?>>) : RowReader {
    private var i = -1
    override fun moveToNext() = ++i < data.size
    override fun columnIndex(name: String) = cols.indexOf(name)
    override fun isNull(column: Int) = data[i][column] == null
    override fun getString(column: Int) = data[i][column]?.toString()
    override fun getLong(column: Int) = (data[i][column] as Number).toLong()
}

class MediaStoreRowsTest {
    private val cols = listOf("_id", "_display_name", "mime_type", "_size", "date_modified")

    @Test
    fun `maps columns and converts seconds to milliseconds`() {
        val rows = MediaStoreRows.toRows(FakeReader(cols, listOf(listOf(42L, "a.jpg", "image/jpeg", 10L, 1_700_000_000L))))
        assertEquals(AlbumRow("42", "a.jpg", "image/jpeg", 10L, 1_700_000_000_000L), rows.single())
    }

    @Test
    fun `row without id keeps null id and null or negative date reads as null`() {
        val rows = MediaStoreRows.toRows(
            FakeReader(cols, listOf(listOf(null, "a", "image/png", 1L, null), listOf(5L, "b", "image/png", 1L, -3L)))
        )
        assertNull(rows[0].documentId)
        assertNull(rows[0].lastModified)
        assertNull(rows[1].lastModified)
        assertEquals(emptyList<String>(), SafAlbumScanner.map(rows.take(1)).map { it.documentId })
    }

    @Test
    fun `empty bucket listing does not resolve`() {
        val l = MediaStoreRows.listing(emptyList())
        assertTrue(!l.loading && !l.rootResolves)
        assertTrue(MediaStoreRows.listing(listOf(AlbumRow("1", "a", "image/jpeg", 1, 1))).rootResolves)
    }
}

class MediaStoreScannerTest {
    private val key = AlbumKey.mediaStore(7, "Camera")
    private val access = FakeAlbumAccess().apply { granted += key }
    private fun rows(n: Int) = (1..n).map { AlbumRow("$it", if (it == 3) "3 - мем.jpg" else "$it.jpg", "image/jpeg", 1, 1000) }
    private fun scanner(a: AlbumAccess = access, list: List<AlbumRow>) =
        SafAlbumScanner({ _, _ -> MediaStoreRows.listing(list) }, a, Dispatchers.Unconfined)

    @Test
    fun `empty bucket is incomplete album unavailable`() = runTest {
        assertEquals(AlbumScanResult.Incomplete("album unavailable"), scanner(list = emptyList()).scan(key))
    }

    @Test
    fun `partial access is permission lost`() = runTest {
        assertEquals(AlbumScanResult.PermissionLost, scanner(FakeAlbumAccess(), rows(3)).scan(key))
    }

    @Test
    fun `three rows are complete with intact names`() = runTest {
        val files = (scanner(list = rows(3)).scan(key) as AlbumScanResult.Complete).files
        assertEquals(listOf("1.jpg", "2.jpg", "3 - мем.jpg"), files.map { it.displayName })
    }
}
