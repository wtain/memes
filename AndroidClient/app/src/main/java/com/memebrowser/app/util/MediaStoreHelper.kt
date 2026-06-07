package com.memebrowser.app.util

import android.content.ContentValues
import android.content.Context
import android.net.Uri
import android.provider.MediaStore
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

suspend fun saveImageToGallery(
    context: Context,
    imageBytes: ByteArray,
    fileName: String,
    mimeType: String = "image/jpeg",
    collection: String
): Uri = withContext(Dispatchers.IO) {
    val values = ContentValues().apply {
        put(MediaStore.Images.Media.DISPLAY_NAME, fileName)
        put(MediaStore.Images.Media.MIME_TYPE, mimeType)
        put(MediaStore.Images.Media.RELATIVE_PATH, "Pictures/MemesBrowser-$collection")
        put(MediaStore.Images.Media.IS_PENDING, 1)
    }
    val resolver = context.contentResolver
    val uri = resolver.insert(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, values)
        ?: error("MediaStore insert returned null")
    try {
        resolver.openOutputStream(uri)!!.use { it.write(imageBytes) }
        values.clear()
        values.put(MediaStore.Images.Media.IS_PENDING, 0)
        resolver.update(uri, values, null, null)
        uri
    } catch (e: Exception) {
        resolver.delete(uri, null, null)
        throw e
    }
}

fun detectMimeType(contentType: String?): Pair<String, String> {
    return when {
        contentType?.contains("png") == true -> "image/png" to "png"
        contentType?.contains("gif") == true -> "image/gif" to "gif"
        contentType?.contains("webp") == true -> "image/webp" to "webp"
        else -> "image/jpeg" to "jpg"
    }
}