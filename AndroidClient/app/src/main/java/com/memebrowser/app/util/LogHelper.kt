package com.memebrowser.app.util

import android.content.Context
import android.content.Intent
import android.os.Process
import androidx.core.content.FileProvider
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File

suspend fun shareAppLogs(context: Context) = withContext(Dispatchers.IO) {
    val pid = Process.myPid()
    val process = Runtime.getRuntime().exec(arrayOf("logcat", "-d", "--pid=$pid", "-v", "time"))
    val logText = process.inputStream.bufferedReader().readText()

    val shareDir = File(context.cacheDir, "share").also { it.mkdirs() }
    val logFile = File(shareDir, "app-logs.txt")
    logFile.writeText(logText)

    val uri = FileProvider.getUriForFile(context, "${context.packageName}.provider", logFile)
    val intent = Intent(Intent.ACTION_SEND).apply {
        type = "text/plain"
        putExtra(Intent.EXTRA_STREAM, uri)
        putExtra(Intent.EXTRA_SUBJECT, "MemesBrowser App Logs")
        addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    }
    context.startActivity(
        Intent.createChooser(intent, "Share Logs").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    )
}