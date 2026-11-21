package com.memebrowser.app.data.sync

import com.memebrowser.app.data.album.AlbumKey
import com.memebrowser.app.data.repository.AlbumFileSource
import java.io.InputStream

/** Routes by key shape: MediaStore bucket keys to [media], SAF tree URIs to [saf]. Exceptions pass through unchanged. */
class DispatchingAlbumFileSource(private val saf: AlbumFileSource, private val media: AlbumFileSource) : AlbumFileSource {
    override fun open(treeUri: String, documentId: String): InputStream =
        (if (AlbumKey.isMediaStore(treeUri)) media else saf).open(treeUri, documentId)
}
