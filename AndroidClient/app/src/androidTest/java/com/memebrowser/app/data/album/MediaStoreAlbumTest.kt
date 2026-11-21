package com.memebrowser.app.data.album

import android.content.ContentUris
import android.content.ContentValues
import android.content.Context
import android.graphics.Bitmap
import android.net.Uri
import android.os.Build
import android.provider.MediaStore
import androidx.test.core.app.ApplicationProvider
import androidx.test.rule.GrantPermissionRule
import com.memebrowser.app.data.repository.AlbumFileSource
import com.memebrowser.app.data.sync.DispatchingAlbumFileSource
import com.memebrowser.app.data.sync.MediaStoreAlbumFileSource
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream

/**
 * The real MediaStore code paths the JVM tests cannot reach (A5): the bucket query, the file open and the
 * seconds-to-milliseconds mapping. Seeds one JPEG into `Pictures/A5Test` and removes it afterwards. Needs an emulator.
 */
class MediaStoreAlbumTest {
    @get:Rule
    val permission: GrantPermissionRule =
        GrantPermissionRule.grant(*MediaAccesses.permissionsToRequest(Build.VERSION.SDK_INT))

    private val context = ApplicationProvider.getApplicationContext<Context>()
    private val resolver = context.contentResolver
    private val fileName = "a5-test-${System.nanoTime()}.jpg"
    private var seededUri: Uri? = null
    private var seededId: Long = -1
    private var bucketId: Long = -1
    private lateinit var bucketName: String

    @Before
    fun seed() {
        val values = ContentValues().apply {
            put(MediaStore.Images.Media.DISPLAY_NAME, fileName)
            put(MediaStore.Images.Media.MIME_TYPE, "image/jpeg")
            put(MediaStore.Images.Media.RELATIVE_PATH, "Pictures/A5Test")
            put(MediaStore.Images.Media.IS_PENDING, 1)
        }
        val uri = requireNotNull(resolver.insert(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, values)) { "insert failed" }
        seededUri = uri
        seededId = ContentUris.parseId(uri)
        resolver.openOutputStream(uri)!!.use { out ->
            val bitmap = Bitmap.createBitmap(32, 32, Bitmap.Config.ARGB_8888)
            bitmap.eraseColor(0xFF3366CC.toInt())
            bitmap.compress(Bitmap.CompressFormat.JPEG, 90, out)
        }
        resolver.update(uri, ContentValues().apply { put(MediaStore.Images.Media.IS_PENDING, 0) }, null, null)
        resolver.query(
            uri,
            arrayOf(MediaStore.Images.Media.BUCKET_ID, MediaStore.Images.Media.BUCKET_DISPLAY_NAME),
            null,
            null,
            null,
        )!!.use {
            assertTrue(it.moveToFirst())
            bucketId = it.getLong(0)
            bucketName = it.getString(1)
        }
    }

    @After
    fun cleanUp() {
        seededUri?.let { resolver.delete(it, null, null) }
    }

    private fun scanner() = SafAlbumScanner(
        DispatchingAlbumRowSource(ContentResolverAlbumRowSource(resolver), MediaStoreAlbumRowSource(resolver)),
        MediaStoreAlbumAccess(ContextMediaAccessSource(context)),
        Dispatchers.IO,
    )

    @Test
    fun listerFindsTheSeededBucket() = runBlocking {
        val lister = MediaStoreAlbumLister(resolver, ContextMediaAccessSource(context), Dispatchers.IO)
        val listing = lister.list()
        assertTrue("listing was $listing", listing is AlbumListing.Albums)
        val album = (listing as AlbumListing.Albums).albums.firstOrNull { it.key == AlbumKey.mediaStore(bucketId, bucketName) }
        assertNotNull("seeded bucket missing", album)
        assertEquals(1, album!!.imageCount)
    }

    @Test
    fun scanReturnsTheSeededFileWithMillisecondTimestamp() = runBlocking {
        val result = scanner().scan(AlbumKey.mediaStore(bucketId, bucketName))
        assertTrue("result was $result", result is AlbumScanResult.Complete)
        val file = (result as AlbumScanResult.Complete).files.single()
        assertEquals(fileName, file.displayName)
        assertEquals(seededId.toString(), file.documentId)
        assertTrue(file.size > 0)
        // DATE_MODIFIED is seconds; a millisecond value is above 10^11 for any date after 1973.
        assertTrue("lastModified ${file.lastModified} looks like seconds", file.lastModified > 100_000_000_000L)
    }

    @Test
    fun fileSourceOpensANonEmptyStream() {
        val stream = MediaStoreAlbumFileSource(resolver).open(AlbumKey.mediaStore(bucketId, bucketName), seededId.toString())
        stream.use { assertTrue(it.readBytes().isNotEmpty()) }
    }

    @Test
    fun emptyBucketMeansTheAlbumIsGone() = runBlocking {
        val key = AlbumKey.mediaStore(bucketId, bucketName)
        resolver.delete(seededUri!!, null, null)
        seededUri = null
        assertEquals(AlbumScanResult.Incomplete("album unavailable"), scanner().scan(key))
    }

    @Test
    fun safKeyStillRoutesToTheSafSource() {
        val payload = byteArrayOf(1, 2, 3)
        val saf = AlbumFileSource { _, _ -> ByteArrayInputStream(payload) }
        val dispatching = DispatchingAlbumFileSource(saf, MediaStoreAlbumFileSource(resolver))
        val bytes = ByteArrayOutputStream().also { out ->
            dispatching.open("content://com.android.externalstorage.documents/tree/primary%3ADCIM", "x").use { it.copyTo(out) }
        }.toByteArray()
        assertTrue(bytes.contentEquals(payload))
    }
}
