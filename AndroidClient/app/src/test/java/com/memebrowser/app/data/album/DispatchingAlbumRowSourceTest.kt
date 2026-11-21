package com.memebrowser.app.data.album

import org.junit.Assert.assertEquals
import org.junit.Test

class DispatchingAlbumRowSourceTest {
    private val saf = AlbumRowSource { _, _ -> RawListing(emptyList(), loading = true) }
    private val media = AlbumRowSource { _, _ -> RawListing(emptyList(), loading = false) }
    private val d = DispatchingAlbumRowSource(saf, media)

    @Test
    fun `media key goes to media`() {
        assertEquals(false, d.list(AlbumKey.mediaStore(1, "X"), ListingCancel())!!.loading)
    }

    @Test
    fun `tree uri goes to saf`() {
        assertEquals(true, d.list("content://x/tree/a", ListingCancel())!!.loading)
    }
}
