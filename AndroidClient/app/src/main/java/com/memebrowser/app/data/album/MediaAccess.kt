package com.memebrowser.app.data.album

/** How much of the photo library the app may read. */
enum class MediaAccess { FULL, PARTIAL, DENIED, BLOCKED }

/** Pure mapping from granted permission names to a [MediaAccess]; no Android framework types. */
object MediaAccesses {
    private const val READ_MEDIA_IMAGES = "android.permission.READ_MEDIA_IMAGES"
    private const val READ_MEDIA_VISUAL_USER_SELECTED = "android.permission.READ_MEDIA_VISUAL_USER_SELECTED"
    private const val READ_EXTERNAL_STORAGE = "android.permission.READ_EXTERNAL_STORAGE"

    fun resolve(granted: Set<String>, sdkInt: Int, canAskAgain: Boolean): MediaAccess {
        val full = if (sdkInt >= 33) READ_MEDIA_IMAGES in granted else READ_EXTERNAL_STORAGE in granted
        return when {
            full -> MediaAccess.FULL
            sdkInt >= 34 && READ_MEDIA_VISUAL_USER_SELECTED in granted -> MediaAccess.PARTIAL
            canAskAgain -> MediaAccess.DENIED
            else -> MediaAccess.BLOCKED
        }
    }

    fun permissionsToRequest(sdkInt: Int): Array<String> = when {
        sdkInt >= 34 -> arrayOf(READ_MEDIA_IMAGES, READ_MEDIA_VISUAL_USER_SELECTED)
        sdkInt >= 33 -> arrayOf(READ_MEDIA_IMAGES)
        else -> arrayOf(READ_EXTERNAL_STORAGE)
    }
}
