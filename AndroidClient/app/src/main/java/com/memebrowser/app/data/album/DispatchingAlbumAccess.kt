package com.memebrowser.app.data.album

/** Routes by key shape: MediaStore bucket keys to [media], SAF tree URIs to [saf]. */
class DispatchingAlbumAccess(private val saf: AlbumAccess, private val media: AlbumAccess) : AlbumAccess {
    private fun pick(key: String) = if (AlbumKey.isMediaStore(key)) media else saf

    override fun persist(treeUri: String): Boolean = pick(treeUri).persist(treeUri)

    override fun isGranted(treeUri: String): Boolean = pick(treeUri).isGranted(treeUri)

    override fun release(treeUri: String) = pick(treeUri).release(treeUri)
}
