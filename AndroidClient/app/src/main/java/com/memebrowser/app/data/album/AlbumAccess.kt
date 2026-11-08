package com.memebrowser.app.data.album

import android.content.ContentResolver
import android.content.Intent
import android.net.Uri

/**
 * Persisted read grants for album trees. Bounded-grants policy: the coordinator releases an album's grant when the
 * album is replaced and no other environment uses the same tree. None of these methods throw.
 */
interface AlbumAccess {
    /** Takes the persistable READ grant the picker returned. False when the system refused it. */
    fun persist(treeUri: String): Boolean

    /** True while [treeUri] is still in the app's persisted read grants (false after the user revoked it). */
    fun isGranted(treeUri: String): Boolean

    /** Drops the persisted grant; a missing grant is not an error. */
    fun release(treeUri: String)
}

class ContentResolverAlbumAccess(private val resolver: ContentResolver) : AlbumAccess {

    override fun persist(treeUri: String): Boolean = try {
        resolver.takePersistableUriPermission(Uri.parse(treeUri), Intent.FLAG_GRANT_READ_URI_PERMISSION)
        true
    } catch (_: RuntimeException) {
        false
    }

    override fun isGranted(treeUri: String): Boolean = try {
        resolver.persistedUriPermissions.any { it.isReadPermission && it.uri.toString() == treeUri }
    } catch (_: RuntimeException) {
        false
    }

    override fun release(treeUri: String) {
        try {
            resolver.releasePersistableUriPermission(Uri.parse(treeUri), Intent.FLAG_GRANT_READ_URI_PERMISSION)
        } catch (_: RuntimeException) {
            // Already gone.
        }
    }
}
