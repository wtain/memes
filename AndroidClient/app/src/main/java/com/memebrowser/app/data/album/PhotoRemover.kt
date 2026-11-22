package com.memebrowser.app.data.album

import android.content.ContentResolver
import android.content.ContentUris
import android.content.IntentSender
import android.os.Build
import android.provider.MediaStore
import com.memebrowser.app.data.tracker.RemovableFile
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.withContext

/**
 * Removes already-uploaded photos from the phone. The seam of the "remove synced photos" feature: the ViewModel talks to
 * this interface only, so its logic runs on the JVM.
 */
interface PhotoRemover {
    /** False where the system cannot ask the user to delete files that belong to other apps (before Android 11). */
    val supported: Boolean

    /**
     * Of [files] (tracker rows of the gallery album [albumKey]) returns the MediaStore ids that still exist, still sit in
     * that album and were not modified after the upload. Nothing else may ever be requested for deletion.
     */
    suspend fun verify(albumKey: String, files: List<RemovableFile>): List<Long>

    /** The system's own "allow the app to delete these N photos?" dialog; deleting happens only if the user allows it. */
    fun requestFor(ids: List<Long>): IntentSender

    companion object {
        /** Ids per system request (a request travels in one Binder transaction, so it must stay small). */
        const val REQUEST_CHUNK = 500
    }
}

/** What MediaStore says about one image right now. */
data class CurrentImage(val modifiedMs: Long, val bucketId: Long)

object RemovalFilter {
    /**
     * The ids of [files] that are in [bucketId] and unchanged since their upload. A file missing from [current], in another
     * bucket, without a usable date or modified after the upload instant is left out (kept on the phone).
     */
    fun unchanged(files: List<RemovableFile>, current: Map<Long, CurrentImage>, bucketId: Long): List<Long> =
        files.mapNotNull { file ->
            val id = file.documentId.toLongOrNull() ?: return@mapNotNull null
            val now = current[id] ?: return@mapNotNull null
            id.takeIf { now.bucketId == bucketId && now.modifiedMs > 0 && now.modifiedMs <= file.uploadedAt }
        }
}

class MediaStoreRemover(
    private val resolver: ContentResolver,
    private val io: CoroutineDispatcher,
) : PhotoRemover {
    override val supported: Boolean get() = Build.VERSION.SDK_INT >= Build.VERSION_CODES.R

    override suspend fun verify(albumKey: String, files: List<RemovableFile>): List<Long> = withContext(io) {
        val bucketId = AlbumKey.bucketId(albumKey) ?: return@withContext emptyList()
        val current = HashMap<Long, CurrentImage>()
        files.mapNotNull { it.documentId.toLongOrNull() }.chunked(QUERY_CHUNK).forEach { chunk ->
            val projection = arrayOf(
                MediaStore.Images.Media._ID,
                MediaStore.Images.Media.DATE_MODIFIED,
                MediaStore.Images.Media.BUCKET_ID,
            )
            val selection = "${MediaStore.Images.Media._ID} IN (${chunk.joinToString(",")})"
            resolver.query(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, projection, selection, null, null)?.use { c ->
                while (c.moveToNext()) {
                    // DATE_MODIFIED is seconds; the tracker's upload instant is milliseconds.
                    current[c.getLong(0)] = CurrentImage(c.getLong(1) * 1000, c.getLong(2))
                }
            }
        }
        RemovalFilter.unchanged(files, current, bucketId)
    }

    override fun requestFor(ids: List<Long>): IntentSender {
        val uris = ids.map { ContentUris.withAppendedId(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, it) }
        return MediaStore.createDeleteRequest(resolver, uris).intentSender
    }

    private companion object {
        const val QUERY_CHUNK = 500
    }
}

/** For tests and screens that never remove anything. */
object NoPhotoRemover : PhotoRemover {
    override val supported: Boolean = false
    override suspend fun verify(albumKey: String, files: List<RemovableFile>): List<Long> = emptyList()
    override fun requestFor(ids: List<Long>): IntentSender = error("removal is not supported")
}
