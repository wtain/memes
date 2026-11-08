package com.memebrowser.app.data.album

import android.content.ContentResolver
import android.database.Cursor
import android.net.Uri
import android.provider.DocumentsContract
import com.memebrowser.app.data.tracker.ScannedFile
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.withContext
import kotlin.coroutines.cancellation.CancellationException

/** Outcome of listing an album. Only [Complete] may be handed to `TrackerRepository.syncScan`. */
sealed interface AlbumScanResult {
    /** The provider answered fully; [files] may legitimately be empty. */
    data class Complete(val files: List<ScannedFile>) : AlbumScanResult

    /** Provider still loading (EXTRA_LOADING), query failed or returned no cursor. Retry later; never sync it. */
    data class Incomplete(val reason: String) : AlbumScanResult

    /** The persisted grant is gone or the provider refused access. The user must pick the album again. */
    data object PermissionLost : AlbumScanResult
}

interface AlbumScanner {
    /** Never throws (except coroutine cancellation). */
    suspend fun scan(treeUri: String): AlbumScanResult
}

/** One raw provider row; every column may be missing. */
data class AlbumRow(
    val documentId: String?,
    val displayName: String?,
    val mimeType: String?,
    val size: Long?,
    val lastModified: Long?,
)

/** What one directory query returned. [loading] mirrors the cursor's EXTRA_LOADING flag. */
class RawListing(val rows: List<AlbumRow>, val loading: Boolean)

/** Blocking, Android-only seam. Returns null when the provider gave no cursor; throws SecurityException on lost access. */
fun interface AlbumRowSource {
    fun list(treeUri: String): RawListing?
}

/**
 * Lists the immediate children of a tree (no recursion, directories skipped) with one query, off the main thread.
 * The decision logic is in [scan] and [map]; the cursor reading is [ContentResolverAlbumRowSource].
 */
class SafAlbumScanner(
    private val source: AlbumRowSource,
    private val access: AlbumAccess,
    private val dispatcher: CoroutineDispatcher,
) : AlbumScanner {

    override suspend fun scan(treeUri: String): AlbumScanResult = try {
        withContext(dispatcher) { scanBlocking(treeUri) }
    } catch (e: CancellationException) {
        throw e
    } catch (e: Exception) {
        AlbumScanResult.Incomplete("listing failed: ${e.javaClass.simpleName}")
    }

    private fun scanBlocking(treeUri: String): AlbumScanResult {
        if (!access.isGranted(treeUri)) return AlbumScanResult.PermissionLost
        val listing = try {
            source.list(treeUri)
        } catch (_: SecurityException) {
            return AlbumScanResult.PermissionLost
        } ?: return AlbumScanResult.Incomplete("no cursor")
        if (listing.loading) return AlbumScanResult.Incomplete("provider still loading")
        return AlbumScanResult.Complete(map(listing.rows))
    }

    companion object {
        private const val DIR_MIME = "vnd.android.document/directory" // DocumentsContract.Document.MIME_TYPE_DIR

        /**
         * Rows to files: no id or a directory mime type means skipped; a null mime type becomes "" (unsupported by
         * `SupportedImages`); a missing size or time becomes 0; a missing name falls back to the last id segment.
         * A repeated document id keeps the first row. (my call)
         */
        fun map(rows: List<AlbumRow>): List<ScannedFile> {
            val seen = HashSet<String>()
            val out = ArrayList<ScannedFile>(rows.size)
            for (row in rows) {
                val id = row.documentId?.takeIf { it.isNotEmpty() } ?: continue
                if (row.mimeType == DIR_MIME) continue
                if (!seen.add(id)) continue
                out += ScannedFile(
                    documentId = id,
                    displayName = row.displayName?.takeIf { it.isNotEmpty() } ?: id.substringAfterLast('/'),
                    mimeType = row.mimeType ?: "",
                    size = row.size?.takeIf { it >= 0 } ?: 0L,
                    lastModified = row.lastModified?.takeIf { it >= 0 } ?: 0L,
                )
            }
            return out
        }
    }
}

/** The real source: one `ContentResolver.query` on the children URI, never `DocumentFile.listFiles()`. */
class ContentResolverAlbumRowSource(private val resolver: ContentResolver) : AlbumRowSource {

    override fun list(treeUri: String): RawListing? {
        val tree = Uri.parse(treeUri)
        val children = DocumentsContract.buildChildDocumentsUriUsingTree(tree, DocumentsContract.getTreeDocumentId(tree))
        val cursor = resolver.query(children, PROJECTION, null, null, null) ?: return null
        cursor.use {
            val loading = it.extras?.getBoolean(DocumentsContract.EXTRA_LOADING, false) ?: false
            val idCol = it.getColumnIndex(DocumentsContract.Document.COLUMN_DOCUMENT_ID)
            val nameCol = it.getColumnIndex(DocumentsContract.Document.COLUMN_DISPLAY_NAME)
            val mimeCol = it.getColumnIndex(DocumentsContract.Document.COLUMN_MIME_TYPE)
            val sizeCol = it.getColumnIndex(DocumentsContract.Document.COLUMN_SIZE)
            val modCol = it.getColumnIndex(DocumentsContract.Document.COLUMN_LAST_MODIFIED)
            val rows = ArrayList<AlbumRow>(it.count.coerceAtLeast(0))
            while (it.moveToNext()) {
                rows += AlbumRow(
                    documentId = it.stringOrNull(idCol),
                    displayName = it.stringOrNull(nameCol),
                    mimeType = it.stringOrNull(mimeCol),
                    size = it.longOrNull(sizeCol),
                    lastModified = it.longOrNull(modCol),
                )
            }
            return RawListing(rows, loading)
        }
    }

    private fun Cursor.stringOrNull(col: Int): String? = if (col < 0 || isNull(col)) null else getString(col)

    private fun Cursor.longOrNull(col: Int): Long? = if (col < 0 || isNull(col)) null else getLong(col)

    private companion object {
        val PROJECTION = arrayOf(
            DocumentsContract.Document.COLUMN_DOCUMENT_ID,
            DocumentsContract.Document.COLUMN_DISPLAY_NAME,
            DocumentsContract.Document.COLUMN_MIME_TYPE,
            DocumentsContract.Document.COLUMN_SIZE,
            DocumentsContract.Document.COLUMN_LAST_MODIFIED,
        )
    }
}
