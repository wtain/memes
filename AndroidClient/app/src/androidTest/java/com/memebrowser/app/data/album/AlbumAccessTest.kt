package com.memebrowser.app.data.album

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test

/**
 * Real ContentResolver behaviour that the JVM tests cannot cover. Needs a device or emulator. The positive path
 * (picking a real album, listing it, surviving a reboot) needs the system picker and is on the owner's hand-check
 * list in the epic tracker (spike S2).
 */
class AlbumAccessTest {
    private val context = ApplicationProvider.getApplicationContext<Context>()
    private val ungranted = "content://com.android.externalstorage.documents/tree/primary%3ADCIM%2FNoSuchAlbum"

    @Test
    fun ungrantedTreeIsNotGranted() {
        assertFalse(ContentResolverAlbumAccess(context.contentResolver).isGranted(ungranted))
    }

    @Test
    fun releasingAMissingGrantDoesNotThrow() {
        ContentResolverAlbumAccess(context.contentResolver).release(ungranted)
    }

    @Test
    fun scanOfAnUngrantedTreeIsPermissionLostAndNeverThrows() = runBlocking {
        val resolver = context.contentResolver
        val scanner = SafAlbumScanner(ContentResolverAlbumRowSource(resolver), ContentResolverAlbumAccess(resolver), Dispatchers.IO)
        assertEquals(AlbumScanResult.PermissionLost, scanner.scan(ungranted))
    }
}
