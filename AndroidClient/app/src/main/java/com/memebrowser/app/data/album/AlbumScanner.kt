package com.memebrowser.app.data.album

import android.content.ContentResolver
import android.database.Cursor
import android.net.Uri
import android.os.CancellationSignal
import android.provider.DocumentsContract
import com.memebrowser.app.data.tracker.ScannedFile
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.async
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withTimeout
import kotlin.coroutines.cancellation.CancellationException

/** Outcome of listing an album. Only [Complete] may be handed to `TrackerRepository.syncScan`. */
sealed interface AlbumScanResult {
    /** The provider answered fully; [files] may legitimately be empty. */
    data class Complete(val files: List<ScannedFile>) : AlbumScanResult

    /** Provider still loading (EXTRA_LOADING), query failed, timed out, root gone or no cursor. Retry later; never sync it. */
    data class Incomplete(val reason: String) : AlbumScanResult

    /** The persisted grant is gone or the provider refused access. The user must pick the album again. */
    data object PermissionLost : AlbumScanResult
}

interface AlbumScanner {
    /** Never throws (except coroutine cancellation). Cancelling the caller aborts the query. */
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

/**
 * What one directory query returned. [loading] mirrors the cursor's EXTRA_LOADING flag. [rootResolves] is only
 * looked at for an EMPTY listing: false means the tree's root document no longer resolves (ejected SD card, deleted
 * folder), so the empty result is not a real "empty album". (my call, review finding 5)
 */
class RawListing(val rows: List<AlbumRow>, val loading: Boolean, val rootResolves: Boolean = true)

/**
 * Lets the scanner abort a blocking query from another thread (a `ContentResolver.query` is not interruptible by
 * coroutine cancellation; the real source hands this to a `CancellationSignal`).
 */
class ListingCancel {
    private var listener: (() -> Unit)? = null
    private var cancelled = false

    @Synchronized
    fun cancel() {
        cancelled = true
        listener?.invoke()
    }

    /** Registers the abort action; runs it at once when [cancel] already happened. */
    @Synchronized
    fun onCancel(action: () -> Unit) {
        listener = action
        if (cancelled) action()
    }
}

/** Blocking, Android-only seam. Returns null when the provider gave no cursor; throws SecurityException on lost access. */
fun interface AlbumRowSource {
    fun list(treeUri: String, cancel: ListingCancel): RawListing?
}

/**
 * Lists the immediate children of a tree (no recursion, directories skipped) with one query, off the main thread.
 * The decision logic is in [scan] and [map]; the cursor reading is [ContentResolverAlbumRowSource].
 *
 * The blocking query runs on a detached task: cancelling [scan] (an album change aborts a hung scan) or hitting
 * [timeoutMillis] aborts it through [ListingCancel] and returns at once, without waiting for the provider.
 */
class SafAlbumScanner(
    private val source: AlbumRowSource,
    private val access: AlbumAccess,
    dispatcher: CoroutineDispatcher,
    private val timeoutMillis: Long = DEFAULT_TIMEOUT_MILLIS,
) : AlbumScanner {

    private val scope = CoroutineScope(SupervisorJob() + dispatcher)

    override suspend fun scan(treeUri: String): AlbumScanResult = try {
        withTimeout(timeoutMillis) {
            val cancel = ListingCancel()
            val work = scope.async { scanBlocking(treeUri, cancel) }
            try {
                work.await()
            } catch (e: CancellationException) {
                cancel.cancel()
                work.cancel()
                throw e
            }
        }
    } catch (e: TimeoutCancellationException) {
        currentCoroutineContext().ensureActive() // an outer cancellation is not a timeout
        AlbumScanResult.Incomplete("listing timed out")
    } catch (e: CancellationException) {
        throw e
    } catch (e: Exception) {
        AlbumScanResult.Incomplete("listing failed: ${e.javaClass.simpleName}")
    }

    private fun scanBlocking(treeUri: String, cancel: ListingCancel): AlbumScanResult {
        if (!access.isGranted(treeUri)) return AlbumScanResult.PermissionLost
        val listing = try {
            source.list(treeUri, cancel)
        } catch (_: SecurityException) {
            return AlbumScanResult.PermissionLost
        } ?: return AlbumScanResult.Incomplete("no cursor")
        if (listing.loading) return AlbumScanResult.Incomplete("provider still loading")
        if (listing.rows.isEmpty() && !listing.rootResolves) return AlbumScanResult.Incomplete("album unavailable")
        return AlbumScanResult.Complete(map(listing.rows))
    }

    companion object {
        const val DEFAULT_TIMEOUT_MILLIS = 60_000L

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
                if (row.mimeType == DocumentsContract.Document.MIME_TYPE_DIR) continue
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

/** Column access over a result set, so [readRows] runs on the JVM with a fake. */
interface RowReader {
    fun moveToNext(): Boolean
    fun columnIndex(name: String): Int
    fun isNull(column: Int): Boolean
    fun getString(column: Int): String?
    fun getLong(column: Int): Long
}

/** Maps every remaining row of [reader]; a column that is missing (index below 0) or null reads as null. */
fun readRows(reader: RowReader): List<AlbumRow> {
    val idCol = reader.columnIndex(DocumentsContract.Document.COLUMN_DOCUMENT_ID)
    val nameCol = reader.columnIndex(DocumentsContract.Document.COLUMN_DISPLAY_NAME)
    val mimeCol = reader.columnIndex(DocumentsContract.Document.COLUMN_MIME_TYPE)
    val sizeCol = reader.columnIndex(DocumentsContract.Document.COLUMN_SIZE)
    val modCol = reader.columnIndex(DocumentsContract.Document.COLUMN_LAST_MODIFIED)
    fun RowReader.str(col: Int): String? = if (col < 0 || isNull(col)) null else getString(col)
    fun RowReader.lng(col: Int): Long? = if (col < 0 || isNull(col)) null else getLong(col)
    val rows = ArrayList<AlbumRow>()
    while (reader.moveToNext()) {
        rows += AlbumRow(reader.str(idCol), reader.str(nameCol), reader.str(mimeCol), reader.lng(sizeCol), reader.lng(modCol))
    }
    return rows
}

/** The real source: one `ContentResolver.query` on the children URI, never `DocumentFile.listFiles()`. */
class ContentResolverAlbumRowSource(private val resolver: ContentResolver) : AlbumRowSource {

    override fun list(treeUri: String, cancel: ListingCancel): RawListing? {
        val signal = CancellationSignal()
        cancel.onCancel { signal.cancel() }
        val tree = Uri.parse(treeUri)
        val treeId = DocumentsContract.getTreeDocumentId(tree)
        val children = DocumentsContract.buildChildDocumentsUriUsingTree(tree, treeId)
        val cursor = resolver.query(children, PROJECTION, null, null, null, signal) ?: return null
        val listing = cursor.use {
            val loading = it.extras?.getBoolean(DocumentsContract.EXTRA_LOADING, false) ?: false
            RawListing(readRows(CursorReader(it)), loading)
        }
        if (listing.rows.isEmpty() && !listing.loading) {
            return RawListing(listing.rows, false, rootResolves = rootResolves(tree, treeId, signal))
        }
        return listing
    }

    /** True when the tree's root document still answers a query (an ejected card or deleted folder does not). */
    private fun rootResolves(tree: Uri, treeId: String, signal: CancellationSignal): Boolean = try {
        val root = DocumentsContract.buildDocumentUriUsingTree(tree, treeId)
        val cursor = resolver.query(root, arrayOf(DocumentsContract.Document.COLUMN_DOCUMENT_ID), null, null, null, signal)
        cursor?.use { it.moveToFirst() } ?: false
    } catch (e: SecurityException) {
        throw e
    } catch (_: RuntimeException) {
        false
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
            DocumentsContract.Document.COLUMN_DOCUMENT_ID,
            DocumentsContract.Document.COLUMN_DISPLAY_NAME,
            DocumentsContract.Document.COLUMN_MIME_TYPE,
            DocumentsContract.Document.COLUMN_SIZE,
            DocumentsContract.Document.COLUMN_LAST_MODIFIED,
        )
    }
}
