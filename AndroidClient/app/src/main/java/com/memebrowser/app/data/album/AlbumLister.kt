package com.memebrowser.app.data.album

import android.content.ContentResolver
import android.database.Cursor
import android.provider.MediaStore
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.withContext

/** One gallery album (a MediaStore bucket). [key] is what the tracker stores as the album id. */
data class GalleryAlbum(
    val key: String,
    val name: String,
    val imageCount: Int,
    val pathHint: String?,
    val newestModified: Long,
)

sealed interface AlbumListing {
    data class Albums(val albums: List<GalleryAlbum>) : AlbumListing
    data object PermissionDenied : AlbumListing
    data object Failed : AlbumListing
}

fun interface AlbumLister {
    /** Never throws except coroutine cancellation. */
    suspend fun list(): AlbumListing
}

/** One image row reduced to what the album chooser needs. [modifiedSeconds] is DATE_MODIFIED (seconds). */
data class BucketRow(
    val bucketId: Long?,
    val bucketName: String?,
    val relativePath: String?,
    val modifiedSeconds: Long?,
)

/** Pure aggregation of image rows into albums, so it runs on the JVM. */
object GalleryAlbums {
    private const val FALLBACK_NAME = "Album"

    fun aggregate(rows: List<BucketRow>): List<GalleryAlbum> {
        val byBucket = LinkedHashMap<Long, MutableList<BucketRow>>()
        for (row in rows) {
            val id = row.bucketId ?: continue
            byBucket.getOrPut(id) { ArrayList() } += row
        }
        return byBucket.map { (id, group) ->
            val newest = group.maxByOrNull { it.modifiedSeconds ?: 0L }!!
            val path = (newest.relativePath ?: "").trim().trimEnd('/').takeIf { it.isNotEmpty() }
            val name = group.firstNotNullOfOrNull { it.bucketName?.takeIf { n -> n.isNotBlank() } }
                ?: path?.substringAfterLast('/')?.takeIf { it.isNotBlank() }
                ?: FALLBACK_NAME
            GalleryAlbum(
                key = AlbumKey.mediaStore(id, name),
                name = name,
                imageCount = group.size,
                pathHint = path,
                newestModified = newest.modifiedSeconds ?: 0L,
            )
        }.sortedWith(compareByDescending<GalleryAlbum> { it.newestModified }.thenBy { it.name })
    }
}

/** One query on the images collection, grouped in memory. Needs full media access. */
class MediaStoreAlbumLister(
    private val resolver: ContentResolver,
    private val access: MediaAccessSource,
    private val dispatcher: CoroutineDispatcher,
) : AlbumLister {

    override suspend fun list(): AlbumListing = withContext(dispatcher) {
        try {
            if (access.current(true) != MediaAccess.FULL) return@withContext AlbumListing.PermissionDenied
            val cursor = resolver.query(
                MediaStore.Images.Media.EXTERNAL_CONTENT_URI,
                PROJECTION,
                "${MediaStore.MediaColumns.IS_PENDING} = ?",
                arrayOf("0"),
                null,
            ) ?: return@withContext AlbumListing.Failed
            val rows = cursor.use { readRows(CursorReader(it)) }
            AlbumListing.Albums(GalleryAlbums.aggregate(rows))
        } catch (e: CancellationException) {
            throw e
        } catch (_: SecurityException) {
            AlbumListing.PermissionDenied
        } catch (_: Exception) {
            AlbumListing.Failed
        }
    }

    private fun readRows(reader: RowReader): List<BucketRow> {
        val idCol = reader.columnIndex(BUCKET_ID)
        val nameCol = reader.columnIndex(BUCKET_NAME)
        val pathCol = reader.columnIndex(RELATIVE_PATH)
        val modCol = reader.columnIndex(MODIFIED)
        fun str(col: Int): String? = if (col < 0 || reader.isNull(col)) null else reader.getString(col)
        fun lng(col: Int): Long? = if (col < 0 || reader.isNull(col)) null else reader.getLong(col)
        val rows = ArrayList<BucketRow>()
        while (reader.moveToNext()) {
            rows += BucketRow(lng(idCol), str(nameCol), str(pathCol), lng(modCol))
        }
        return rows
    }

    private class CursorReader(private val c: Cursor) : RowReader {
        override fun moveToNext() = c.moveToNext()
        override fun columnIndex(name: String) = c.getColumnIndex(name)
        override fun isNull(column: Int) = c.isNull(column)
        override fun getString(column: Int): String? = c.getString(column)
        override fun getLong(column: Int) = c.getLong(column)
    }

    private companion object {
        const val BUCKET_ID = MediaStore.Images.Media.BUCKET_ID
        const val BUCKET_NAME = MediaStore.Images.Media.BUCKET_DISPLAY_NAME
        const val RELATIVE_PATH = MediaStore.MediaColumns.RELATIVE_PATH
        const val MODIFIED = MediaStore.Images.Media.DATE_MODIFIED
        val PROJECTION = arrayOf(BUCKET_ID, BUCKET_NAME, RELATIVE_PATH, MODIFIED)
    }
}
