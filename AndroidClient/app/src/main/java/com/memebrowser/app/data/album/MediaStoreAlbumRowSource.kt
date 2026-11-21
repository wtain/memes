package com.memebrowser.app.data.album

import android.content.ContentResolver
import android.database.Cursor
import android.os.CancellationSignal
import android.provider.MediaStore

/** Pure mapping of MediaStore image rows (column names only), so it runs on the JVM with a fake [RowReader]. */
object MediaStoreRows {
    private const val ID = "_id"
    private const val NAME = "_display_name"
    private const val MIME = "mime_type"
    private const val SIZE = "_size"
    private const val MODIFIED = "date_modified"

    /** `documentId` is the decimal `_ID`; DATE_MODIFIED is seconds, the tracker uses milliseconds. */
    fun toRows(reader: RowReader): List<AlbumRow> {
        val idCol = reader.columnIndex(ID)
        val nameCol = reader.columnIndex(NAME)
        val mimeCol = reader.columnIndex(MIME)
        val sizeCol = reader.columnIndex(SIZE)
        val modCol = reader.columnIndex(MODIFIED)
        fun RowReader.str(col: Int): String? = if (col < 0 || isNull(col)) null else getString(col)
        fun RowReader.lng(col: Int): Long? = if (col < 0 || isNull(col)) null else getLong(col)
        val rows = ArrayList<AlbumRow>()
        while (reader.moveToNext()) {
            val seconds = reader.lng(modCol)?.takeIf { it >= 0 }
            rows += AlbumRow(
                documentId = reader.lng(idCol)?.toString(),
                displayName = reader.str(nameCol),
                mimeType = reader.str(mimeCol),
                size = reader.lng(sizeCol),
                lastModified = seconds?.times(1000),
            )
        }
        return rows
    }

    /** An empty bucket means the album is gone, so the scanner reports "album unavailable". */
    fun listing(rows: List<AlbumRow>): RawListing = RawListing(rows, loading = false, rootResolves = rows.isNotEmpty())
}

/** One query on the images collection for one bucket. */
class MediaStoreAlbumRowSource(private val resolver: ContentResolver) : AlbumRowSource {

    override fun list(treeUri: String, cancel: ListingCancel): RawListing? {
        val bucketId = AlbumKey.bucketId(treeUri) ?: return null
        val signal = CancellationSignal()
        cancel.onCancel { signal.cancel() }
        val cursor = resolver.query(
            MediaStore.Images.Media.EXTERNAL_CONTENT_URI,
            PROJECTION,
            "${MediaStore.Images.Media.BUCKET_ID} = ? AND ${MediaStore.MediaColumns.IS_PENDING} = 0",
            arrayOf(bucketId.toString()),
            null,
            signal,
        ) ?: return null
        return cursor.use { MediaStoreRows.listing(MediaStoreRows.toRows(CursorReader(it))) }
    }

    private class CursorReader(private val c: Cursor) : RowReader {
        override fun moveToNext() = c.moveToNext()
        override fun columnIndex(name: String) = c.getColumnIndex(name)
        override fun isNull(column: Int) = c.isNull(column)
        override fun getString(column: Int): String? = c.getString(column)
        override fun getLong(column: Int) = c.getLong(column)
    }

    private companion object {
        val PROJECTION = arrayOf(
            MediaStore.Images.Media._ID,
            MediaStore.Images.Media.DISPLAY_NAME,
            MediaStore.Images.Media.MIME_TYPE,
            MediaStore.Images.Media.SIZE,
            MediaStore.Images.Media.DATE_MODIFIED,
        )
    }
}
