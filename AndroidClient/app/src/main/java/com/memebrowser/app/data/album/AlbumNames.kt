package com.memebrowser.app.data.album

import java.net.URLDecoder

/**
 * Pure helpers on the string form of a SAF tree URI (`content://<authority>/tree/<encoded document id>`).
 * They avoid `android.net.Uri` so they run on the JVM.
 */
object AlbumNames {
    private const val TREE_MARKER = "/tree/"
    private const val DOCUMENT_MARKER = "/document/"
    private const val EXTERNAL_STORAGE_AUTHORITY = "com.android.externalstorage.documents"

    private fun authority(treeUri: String): String? {
        val start = treeUri.indexOf("://")
        if (start < 0) return null
        return treeUri.substring(start + 3).substringBefore('/').takeIf { it.isNotEmpty() }
    }

    /** The decoded tree document id (for example `primary:DCIM/Camera`), or null when [treeUri] has no tree part. */
    fun treeDocumentId(treeUri: String): String? {
        val start = treeUri.indexOf(TREE_MARKER)
        if (start < 0) return null
        val rest = treeUri.substring(start + TREE_MARKER.length)
        val encoded = rest.substringBefore(DOCUMENT_MARKER).substringBefore('?')
        if (encoded.isEmpty()) return null
        return try {
            URLDecoder.decode(encoded.replace("+", "%2B"), "UTF-8")
        } catch (_: IllegalArgumentException) {
            null
        }
    }

    /**
     * Human name of the album: the last path segment of the tree document id (`primary:DCIM/Camera` gives `Camera`).
     * A volume root (`primary:`) is called "Internal storage" for `primary`, otherwise the volume id. Falls back to
     * the raw [treeUri] when no id can be read. (my call)
     */
    fun displayName(treeUri: String): String {
        val id = treeDocumentId(treeUri) ?: return treeUri
        val volume = id.substringBefore(':')
        val path = if (':' in id) id.substringAfter(':') else id
        val last = path.split('/').lastOrNull { it.isNotBlank() }
        return when {
            last != null -> last
            volume.equals("primary", ignoreCase = true) -> "Internal storage"
            volume.isNotBlank() -> volume
            else -> treeUri
        }
    }

    /**
     * True when the folder is one of the app's own gallery folders (`Pictures/MemesBrowser-<collection>`, written by
     * MediaStoreHelper) or inside one. Syncing it would send the library back to itself, so A4 warns. Only the
     * external-storage authority is checked: other providers (Downloads, cloud) never hold these folders. (my call)
     */
    fun looksLikeAppOwnFolder(treeUri: String): Boolean {
        if (authority(treeUri) != EXTERNAL_STORAGE_AUTHORITY) return false
        val id = treeDocumentId(treeUri) ?: return false
        val path = if (':' in id) id.substringAfter(':') else id
        val segments = path.split('/').filter { it.isNotBlank() }
        return segments.zipWithNext().any { (parent, child) ->
            parent.equals("Pictures", ignoreCase = true) && child.startsWith("MemesBrowser-", ignoreCase = true)
        }
    }
}
