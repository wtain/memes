package com.memebrowser.app.data.album

import java.net.URLDecoder
import java.net.URLEncoder

/**
 * Pure helpers on the key of a MediaStore album: `mediastore-bucket:<BUCKET_ID>/<URL-encoded bucket name>`.
 * The key shares the `albumTreeUri` column with SAF tree URIs. No `android.net.Uri`, so it runs on the JVM.
 */
object AlbumKey {
    private const val PREFIX = "mediastore-bucket:"

    fun mediaStore(bucketId: Long, bucketName: String): String =
        PREFIX + bucketId + "/" + URLEncoder.encode(bucketName, "UTF-8")

    fun isMediaStore(key: String): Boolean = key.startsWith(PREFIX)

    /** The bucket id, or null for a SAF key or a malformed key. */
    fun bucketId(key: String): Long? {
        if (!isMediaStore(key)) return null
        val rest = key.substring(PREFIX.length)
        if ('/' !in rest) return null
        return rest.substringBefore('/').toLongOrNull()
    }

    /** The decoded bucket name, or null for a SAF key or a malformed key. */
    fun bucketName(key: String): String? {
        if (bucketId(key) == null) return null
        val encoded = key.substring(PREFIX.length).substringAfter('/')
        return try {
            URLDecoder.decode(encoded, "UTF-8")
        } catch (_: IllegalArgumentException) {
            null
        }
    }
}
