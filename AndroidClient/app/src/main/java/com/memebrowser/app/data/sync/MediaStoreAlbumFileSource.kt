package com.memebrowser.app.data.sync

import android.content.ContentResolver
import android.content.ContentUris
import android.provider.MediaStore
import com.memebrowser.app.data.repository.AlbumFileSource
import java.io.FileNotFoundException
import java.io.IOException
import java.io.InputStream

/** Opens a MediaStore image by its `_ID` (the document id, a decimal string); streams, never buffers. */
class MediaStoreAlbumFileSource(private val resolver: ContentResolver) : AlbumFileSource {
    override fun open(treeUri: String, documentId: String): InputStream {
        val id = documentId.toLongOrNull() ?: throw FileNotFoundException("not a MediaStore id: $documentId")
        val uri = ContentUris.withAppendedId(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, id)
        // FileNotFoundException (vanished) and SecurityException (permission revoked) come from the provider unchanged.
        // A null stream is transient: a plain IOException, like ContentResolverAlbumFileSource.
        return resolver.openInputStream(uri) ?: throw IOException("provider returned no stream for $documentId")
    }
}
