package com.memebrowser.app.data.repository

import android.content.Context
import android.util.Log
import com.memebrowser.app.data.model.BackendEnvironment
import com.memebrowser.app.util.buildLogFile
import com.memebrowser.app.util.shareLogFile
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.asRequestBody
import java.io.File
import java.util.concurrent.TimeUnit
import javax.inject.Inject
import javax.inject.Singleton

private const val TAG = "BugReportUploader"

/**
 * Sends the combined app log to the backend, trying the currently selected
 * environment first and falling back to the others in order. If every
 * environment fails, falls back to the share-sheet as a last resort.
 *
 * Uses a plain OkHttpClient (not the Hilt-provided one) because NetworkModule's
 * client rewrites every request's host to the currently selected environment —
 * unsuitable here, since we need to reach specific non-selected environments too.
 */
@Singleton
class BugReportUploader @Inject constructor(
    private val environmentRepository: EnvironmentRepository
) {
    private val plainClient = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .build()

    suspend fun sendBugReport(context: Context, onMessage: suspend (String) -> Unit) {
        val logFile = buildLogFile(context)
        val environments = orderedEnvironments()

        if (environments.isEmpty()) {
            onMessage("No backend environment configured — sharing instead")
            shareLogFile(context, logFile)
            return
        }

        environments.forEachIndexed { index, env ->
            onMessage("Uploading to ${env.name}…")
            val uploaded = withContext(Dispatchers.IO) { uploadTo(env, logFile) }
            if (uploaded) {
                onMessage("Bug report sent to ${env.name}")
                return
            }
            val hasNext = index < environments.lastIndex
            onMessage(if (hasNext) "${env.name} failed, trying next environment…" else "${env.name} failed")
        }

        onMessage("Upload failed on all environments — sharing instead")
        shareLogFile(context, logFile)
    }

    private suspend fun orderedEnvironments(): List<BackendEnvironment> {
        val withSelection = environmentRepository.environmentsWithSelection.first()
        val selected = withSelection.firstOrNull { it.isSelected }?.environment
        val rest = withSelection.map { it.environment }.filter { it.id != selected?.id }
        return listOfNotNull(selected) + rest
    }

    private fun uploadTo(env: BackendEnvironment, logFile: File): Boolean {
        return try {
            val requestBody = logFile.asRequestBody("text/plain".toMediaType())
            val body = MultipartBody.Builder()
                .setType(MultipartBody.FORM)
                .addFormDataPart("file", logFile.name, requestBody)
                .build()
            val url = env.baseUrl.trimEnd('/') + "/api/bug-reports"
            val request = Request.Builder().url(url).post(body).build()
            plainClient.newCall(request).execute().use { response -> response.isSuccessful }
        } catch (e: Exception) {
            Log.e(TAG, "Upload to ${env.name} failed", e)
            false
        }
    }
}