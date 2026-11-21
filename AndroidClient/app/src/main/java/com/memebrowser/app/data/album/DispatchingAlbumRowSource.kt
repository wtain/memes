package com.memebrowser.app.data.album

/** Routes by key shape: MediaStore bucket keys to [media], SAF tree URIs to [saf]. */
class DispatchingAlbumRowSource(private val saf: AlbumRowSource, private val media: AlbumRowSource) : AlbumRowSource {
    override fun list(treeUri: String, cancel: ListingCancel): RawListing? =
        (if (AlbumKey.isMediaStore(treeUri)) media else saf).list(treeUri, cancel)
}
