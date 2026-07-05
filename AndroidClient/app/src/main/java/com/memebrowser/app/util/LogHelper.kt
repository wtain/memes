package com.memebrowser.app.util

import android.content.Context
import android.content.Intent
import android.os.Process
import androidx.core.content.FileProvider
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

suspend fun buildLogFile(context: Context): File = withContext(Dispatchers.IO) {
    val pid = Process.myPid()
    val now = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US).format(Date())

    val process = Runtime.getRuntime().exec(arrayOf("logcat", "-d", "--pid=$pid", "-v", "time"))
    val logcatText = process.inputStream.bufferedReader().readText()

    val savedCrashes = File(context.filesDir, "crashes")
        .takeIf { it.isDirectory }
        ?.listFiles()
        ?.sortedByDescending { it.lastModified() }
        ?.joinToString("\n\n") { it.readText() }
        .orEmpty()

    val combined = buildString {
        appendLine("=== Current session (pid $pid, $now) ===")
        appendLine()
        append(logcatText)
        if (savedCrashes.isNotEmpty()) {
            appendLine()
            appendLine("=== Saved crash reports ===")
            appendLine()
            append(savedCrashes)
        }
    }

    val shareDir = File(context.cacheDir, "share").also { it.mkdirs() }
    val fileTimestamp = SimpleDateFormat("yyyyMMdd-HHmmss", Locale.US).format(Date())
    val logFile = File(shareDir, "app-logs-$fileTimestamp.txt")
    logFile.writeText(combined)
    logFile
}

suspend fun shareLogFile(context: Context, logFile: File) = withContext(Dispatchers.IO) {
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