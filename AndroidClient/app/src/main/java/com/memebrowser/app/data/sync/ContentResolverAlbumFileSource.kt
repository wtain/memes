package com.memebrowser.app.data.sync

import android.content.ContentResolver
import android.net.Uri
import android.provider.DocumentsContract
import com.memebrowser.app.data.repository.AlbumFileSource
import java.io.FileNotFoundException
import java.io.InputStream

/** Opens `buildDocumentUriUsingTree(tree, documentId)` through the persisted tree grant; streams, never buffers. */
class ContentResolverAlbumFileSource(private val resolver: ContentResolver) : AlbumFileSource {
    override fun open(treeUri: String, documentId: String): InputStream {
        val uri = DocumentsContract.buildDocumentUriUsingTree(Uri.parse(treeUri), documentId)
        return resolver.openInputStream(uri) ?: throw FileNotFoundException("no stream for $documentId")
    }
}
