package com.memebrowser.app.data.album

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class AlbumNamesTest {
    private fun tree(id: String) = "content://com.android.externalstorage.documents/tree/$id"

    @Test
    fun `last path segment is the name`() {
        assertEquals("Camera", AlbumNames.displayName(tree("primary%3ADCIM%2FCamera")))
    }

    @Test
    fun `trailing slashes are ignored`() {
        assertEquals("Camera", AlbumNames.displayName(tree("primary%3ADCIM%2FCamera%2F")))
    }

    @Test
    fun `cyrillic and spaces are decoded`() {
        assertEquals("Мемы 2024", AlbumNames.displayName(tree("primary%3APictures%2F%D0%9C%D0%B5%D0%BC%D1%8B%202024")))
    }

    @Test
    fun `plus sign is literal`() {
        assertEquals("a+b", AlbumNames.displayName(tree("primary%3ADCIM%2Fa%2Bb")))
    }

    @Test
    fun `document suffix of a document tree uri is cut off`() {
        val uri = tree("primary%3ADCIM%2FCamera") + "/document/primary%3ADCIM%2FCamera%2Fx.jpg"
        assertEquals("Camera", AlbumNames.displayName(uri))
    }

    @Test
    fun `volume roots`() {
        assertEquals("Internal storage", AlbumNames.displayName(tree("primary%3A")))
        assertEquals("1A2B-3C4D", AlbumNames.displayName(tree("1A2B-3C4D%3A")))
    }

    @Test
    fun `unparseable uri falls back to itself`() {
        assertEquals("content://x/y", AlbumNames.displayName("content://x/y"))
        assertEquals("", AlbumNames.displayName(""))
        assertNull(AlbumNames.treeDocumentId("content://x/tree/"))
    }

    @Test
    fun `malformed escape falls back to the uri`() {
        val uri = tree("primary%3ADCIM%2F%ZZ")
        assertEquals(uri, AlbumNames.displayName(uri))
    }

    @Test
    fun `app own folders are flagged`() {
        assertTrue(AlbumNames.looksLikeAppOwnFolder(tree("primary%3APictures%2FMemesBrowser-general")))
        assertTrue(AlbumNames.looksLikeAppOwnFolder(tree("primary%3APictures%2Fmemesbrowser-metal%2F")))
        assertTrue(AlbumNames.looksLikeAppOwnFolder(tree("primary%3APictures%2FMemesBrowser-it%2Fsub")))
    }

    @Test
    fun `other folders are not flagged`() {
        assertFalse(AlbumNames.looksLikeAppOwnFolder(tree("primary%3ADCIM%2FCamera")))
        assertFalse(AlbumNames.looksLikeAppOwnFolder(tree("primary%3APictures")))
        assertFalse(AlbumNames.looksLikeAppOwnFolder(tree("primary%3ADCIM%2FMemesBrowser-general")))
        assertFalse(AlbumNames.looksLikeAppOwnFolder("not a uri"))
    }

    @Test
    fun `only the external storage authority triggers the own folder warning`() {
        val id = "primary%3APictures%2FMemesBrowser-general"
        assertFalse(AlbumNames.looksLikeAppOwnFolder("content://com.android.providers.downloads.documents/tree/$id"))
        assertFalse(AlbumNames.looksLikeAppOwnFolder("content://x/tree/$id"))
    }
}
