package com.memebrowser.app.data.album

import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.content.ContextCompat

/** Current photo-library access. A seam because `Build.VERSION.SDK_INT` is 0 in JVM tests. */
fun interface MediaAccessSource {
    fun current(canAskAgain: Boolean): MediaAccess
}

class ContextMediaAccessSource(
    private val context: Context,
    private val sdkInt: Int = Build.VERSION.SDK_INT,
) : MediaAccessSource {

    override fun current(canAskAgain: Boolean): MediaAccess {
        val granted = PERMISSIONS.filterTo(HashSet()) {
            ContextCompat.checkSelfPermission(context, it) == PackageManager.PERMISSION_GRANTED
        }
        return MediaAccesses.resolve(granted, sdkInt, canAskAgain)
    }

    private companion object {
        val PERMISSIONS = listOf(
            "android.permission.READ_MEDIA_IMAGES",
            "android.permission.READ_MEDIA_VISUAL_USER_SELECTED",
            "android.permission.READ_EXTERNAL_STORAGE",
        )
    }
}

/** MediaStore albums need no grant of their own: only full library access counts. None of these methods throw. */
class MediaStoreAlbumAccess(private val access: MediaAccessSource) : AlbumAccess {
    override fun persist(treeUri: String): Boolean = full()

    override fun isGranted(treeUri: String): Boolean = full()

    override fun release(treeUri: String) {
        // Nothing to release.
    }

    private fun full(): Boolean = try {
        access.current(canAskAgain = true) == MediaAccess.FULL
    } catch (_: RuntimeException) {
        false
    }
}
