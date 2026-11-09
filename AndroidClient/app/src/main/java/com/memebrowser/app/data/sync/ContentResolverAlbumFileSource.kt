package com.memebrowser.app.data.sync

import android.content.ContentResolver
import android.net.Uri
import android.provider.DocumentsContract
import com.memebrowser.app.data.repository.AlbumFileSource
import java.io.IOException
import java.io.InputStream

/** Opens `buildDocumentUriUsingTree(tree, documentId)` through the persisted tree grant; streams, never buffers. */
class ContentResolverAlbumFileSource(private val resolver: ContentResolver) : AlbumFileSource {
    override fun open(treeUri: String, documentId: String): InputStream {
        val uri = DocumentsContract.buildDocumentUriUsingTree(Uri.parse(treeUri), documentId)
        // A null stream means the provider is (re)starting, not that the file is gone: a plain IOException, so the engine
        // treats it as transient. Only a FileNotFoundException from the provider means "the document no longer exists".
        return resolver.openInputStream(uri) ?: throw IOException("provider returned no stream for $documentId")
    }
}
