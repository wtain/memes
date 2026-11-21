package com.memebrowser.app.data.album

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class AlbumKeyTest {
    @Test
    fun `round trip`() {
        val key = AlbumKey.mediaStore(123, "3 - memes")
        assertTrue(AlbumKey.isMediaStore(key))
        assertEquals(123L, AlbumKey.bucketId(key))
        assertEquals("3 - memes", AlbumKey.bucketName(key))
    }

    @Test
    fun `slash percent and cyrillic round trip`() {
        val key = AlbumKey.mediaStore(-7, "Мемы/100%")
        assertEquals(-7L, AlbumKey.bucketId(key))
        assertEquals("Мемы/100%", AlbumKey.bucketName(key))
    }

    @Test
    fun `saf key is not media store`() {
        val saf = "content://com.android.externalstorage.documents/tree/primary%3ADCIM"
        assertFalse(AlbumKey.isMediaStore(saf))
        assertNull(AlbumKey.bucketId(saf))
        assertNull(AlbumKey.bucketName(saf))
    }

    @Test
    fun `malformed keys give null`() {
        assertNull(AlbumKey.bucketId("mediastore-bucket:abc/x"))
        assertNull(AlbumKey.bucketId("mediastore-bucket:12"))
        assertNull(AlbumKey.bucketName("mediastore-bucket:12"))
        assertNull(AlbumKey.bucketName("mediastore-bucket:12/%ZZ"))
    }
}
