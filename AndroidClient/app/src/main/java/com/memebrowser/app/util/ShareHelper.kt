package com.memebrowser.app.util

import android.content.Context
import android.content.Intent
import androidx.core.content.FileProvider
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File

suspend fun shareImage(
    context: Context,
    imageBytes: ByteArray,
    memeId: String,
    extension: String = "jpg",
    mimeType: String = "image/jpeg"
) = withContext(Dispatchers.IO) {
    val shareDir = File(context.cacheDir, "share").also { it.mkdirs() }
    val file = File(shareDir, "$memeId.$extension")
    if (!file.exists() || file.length() == 0L) {
        file.writeBytes(imageBytes)
    }
    val uri = FileProvider.getUriForFile(context, "${context.packageName}.provider", file)
    val intent = Intent(Intent.ACTION_SEND).apply {
        type = mimeType
        putExtra(Intent.EXTRA_STREAM, uri)
        addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    }
    context.startActivity(Intent.createChooser(intent, null).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
}