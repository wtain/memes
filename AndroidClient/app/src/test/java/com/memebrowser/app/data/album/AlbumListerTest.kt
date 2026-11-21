package com.memebrowser.app.data.album

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class AlbumListerTest {

    private fun row(id: Long?, name: String?, path: String?, mod: Long?) = BucketRow(id, name, path, mod)

    @Test
    fun threeRowsInTwoBucketsAggregateNewestFirst() {
        val albums = GalleryAlbums.aggregate(
            listOf(
                row(1, "Camera", "DCIM/Camera/", 100),
                row(2, "Memes", "Pictures/Memes/", 300),
                row(1, "Camera", "DCIM/Camera/", 200),
            ),
        )
        assertEquals(listOf("Memes", "Camera"), albums.map { it.name })
        assertEquals(listOf(1, 2), albums.map { it.imageCount })
        assertEquals(listOf(300L, 200L), albums.map { it.newestModified })
    }

    @Test
    fun sameNameBucketsStayDistinguishableByPathHint() {
        val albums = GalleryAlbums.aggregate(
            listOf(
                row(1, "Screenshots", "Pictures/Screenshots/", 200),
                row(2, "Screenshots", "DCIM/Screenshots/", 100),
            ),
        )
        assertEquals(listOf("Pictures/Screenshots", "DCIM/Screenshots"), albums.map { it.pathHint })
        assertEquals(2, albums.map { it.key }.toSet().size)
    }

    @Test
    fun rowWithoutBucketIdIsIgnored() {
        val albums = GalleryAlbums.aggregate(listOf(row(null, "X", "a/", 1), row(5, "Y", "b/", 2)))
        assertEquals(1, albums.size)
        assertEquals("Y", albums[0].name)
    }

    @Test
    fun keyEqualsAlbumKeyMediaStore() {
        val albums = GalleryAlbums.aggregate(listOf(row(7, "My Album", "Pictures/My Album/", 1)))
        assertEquals(AlbumKey.mediaStore(7, "My Album"), albums[0].key)
    }

    @Test
    fun blankNameFallsBackToPathSegmentThenAlbum() {
        val albums = GalleryAlbums.aggregate(
            listOf(
                row(1, "", "Pictures/Foo/", 3),
                row(2, "  ", null, 2),
                row(3, null, "", 1),
            ),
        )
        assertEquals(listOf("Foo", "Album", "Album"), albums.map { it.name })
        assertEquals(AlbumKey.mediaStore(2, "Album"), albums[1].key)
        assertNull(albums[1].pathHint)
    }

    @Test
    fun pathHintComesFromNewestRowAndTiesSortByName() {
        val albums = GalleryAlbums.aggregate(
            listOf(
                row(1, "B", "old/", 1),
                row(1, "B", "new/", 9),
                row(2, "A", "a/", 9),
                row(3, "C", "c/", null),
            ),
        )
        assertEquals(listOf("A", "B", "C"), albums.map { it.name })
        assertEquals("new", albums[1].pathHint)
        assertEquals(0L, albums[2].newestModified)
    }
}
