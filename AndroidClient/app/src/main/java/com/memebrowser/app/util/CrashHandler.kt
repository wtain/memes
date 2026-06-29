package com.memebrowser.app.util

import android.content.Context
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

object CrashHandler : Thread.UncaughtExceptionHandler {

    private lateinit var appContext: Context
    private var defaultHandler: Thread.UncaughtExceptionHandler? = null

    fun install(context: Context) {
        appContext = context.applicationContext
        defaultHandler = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler(this)
    }

    override fun uncaughtException(thread: Thread, throwable: Throwable) {
        try {
            writeCrashFile(thread, throwable)
        } catch (_: Throwable) {
            // Never let the crash handler itself prevent the default handler from running.
        } finally {
            defaultHandler?.uncaughtException(thread, throwable)
        }
    }

    private fun writeCrashFile(thread: Thread, throwable: Throwable) {
        val crashDir = File(appContext.filesDir, "crashes").also { it.mkdirs() }
        // Keep the last 5 crash reports; delete the oldest first.
        crashDir.listFiles()
            ?.sortedByDescending { it.lastModified() }
            ?.drop(4)
            ?.forEach { it.delete() }

        val timestamp = System.currentTimeMillis()
        val dateStr = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US).format(Date(timestamp))
        File(crashDir, "crash-$timestamp.txt").writeText(buildString {
            appendLine("=== Crash at $dateStr (thread: ${thread.name}) ===")
            appendLine()
            append(throwable.stackTraceToString())
        })
    }
}
