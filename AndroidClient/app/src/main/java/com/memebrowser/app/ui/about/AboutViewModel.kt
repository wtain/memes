package com.memebrowser.app.ui.about

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.Settings
import android.util.Log
import androidx.core.content.FileProvider
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.memebrowser.app.BuildConfig
import com.memebrowser.app.data.repository.BugReportUploader
import dagger.hilt.android.lifecycle.HiltViewModel
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import okhttp3.OkHttpClient
import okhttp3.Request
import okio.buffer
import okio.sink
import java.io.File
import java.util.concurrent.TimeUnit
import javax.inject.Inject

private const val TAG = "AboutViewModel"
private const val GITHUB_REPO = "wtain/memes"
private const val RELEASES_API = "https://api.github.com/repos/$GITHUB_REPO/releases?per_page=50"
const val GITHUB_URL = "https://github.com/$GITHUB_REPO"

@Serializable
private data class GitHubRelease(
    @SerialName("tag_name") val tagName: String,
    @SerialName("assets") val assets: List<GitHubAsset>
)

@Serializable
private data class GitHubAsset(
    @SerialName("name") val name: String,
    @SerialName("browser_download_url") val browserDownloadUrl: String,
    @SerialName("content_type") val contentType: String
)

sealed class UpdateStatus {
    object Idle : UpdateStatus()
    object Checking : UpdateStatus()
    object UpToDate : UpdateStatus()
    data class Available(val tag: String, val apkUrl: String) : UpdateStatus()
    data class Downloading(val progress: Float) : UpdateStatus()
    object ReadyToInstall : UpdateStatus()
    data class Error(val message: String) : UpdateStatus()
}

@HiltViewModel
class AboutViewModel @Inject constructor(
    @ApplicationContext private val appContext: Context,
    private val bugReportUploader: BugReportUploader
) : ViewModel() {

    val currentVersion: String = BuildConfig.ANDROID_VERSION

    private val _updateStatus = MutableStateFlow<UpdateStatus>(UpdateStatus.Idle)
    val updateStatus: StateFlow<UpdateStatus> = _updateStatus.asStateFlow()

    private val _bugReportMessage = MutableStateFlow<String?>(null)
    val bugReportMessage: StateFlow<String?> = _bugReportMessage.asStateFlow()

    fun sendBugReport() {
        viewModelScope.launch {
            bugReportUploader.sendBugReport(appContext) { message ->
                _bugReportMessage.value = message
            }
        }
    }

    fun dismissBugReportMessage() {
        _bugReportMessage.value = null
    }

    // Plain client — does NOT go through the backend-rewriting interceptor
    private val githubClient = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .build()

    private val json = Json { ignoreUnknownKeys = true }

    fun checkForUpdates() {
        if (_updateStatus.value is UpdateStatus.Checking) return
        viewModelScope.launch {
            _updateStatus.value = UpdateStatus.Checking
            runCatching {
                withContext(Dispatchers.IO) {
                    val request = Request.Builder().url(RELEASES_API)
                        .header("Accept", "application/vnd.github+json")
                        .build()
                    val body = githubClient.newCall(request).execute().use { response ->
                        if (!response.isSuccessful) error("GitHub API ${response.code}")
                        response.body?.string() ?: error("Empty response")
                    }
                    val releases = json.decodeFromString<List<GitHubRelease>>(body)
                    releases.firstOrNull { it.tagName.startsWith("android-v") }
                }
            }.onSuccess { latest ->
                if (latest == null) {
                    _updateStatus.value = UpdateStatus.UpToDate
                    return@onSuccess
                }
                val apkAsset = latest.assets.firstOrNull {
                    it.name.endsWith(".apk") || it.contentType == "application/vnd.android.package-archive"
                }
                if (apkAsset == null || !isNewer(latest.tagName, currentVersion)) {
                    _updateStatus.value = UpdateStatus.UpToDate
                } else {
                    _updateStatus.value = UpdateStatus.Available(latest.tagName, apkAsset.browserDownloadUrl)
                }
            }.onFailure { e ->
                Log.e(TAG, "Update check failed", e)
                _updateStatus.value = UpdateStatus.Error(e.message ?: "Update check failed")
            }
        }
    }

    fun downloadUpdate(apkUrl: String, tag: String) {
        if (!appContext.packageManager.canRequestPackageInstalls()) {
            appContext.startActivity(
                Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES)
                    .setData(Uri.parse("package:${appContext.packageName}"))
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            )
            _updateStatus.value = UpdateStatus.Error(
                "Enable 'Install unknown apps' for MemeBrowser in Settings, then try again"
            )
            return
        }
        viewModelScope.launch(Dispatchers.IO) {
            _updateStatus.update { UpdateStatus.Downloading(0f) }
            runCatching {
                val request = Request.Builder().url(apkUrl).build()
                githubClient.newCall(request).execute().use { response ->
                    if (!response.isSuccessful) error("Download failed: HTTP ${response.code}")
                    val body = response.body ?: error("Empty download body")
                    val total = body.contentLength().takeIf { it > 0 }
                    val apkFile = File(appContext.getExternalFilesDir(null), "memebrowser-update.apk")
                    var written = 0L
                    body.source().use { source ->
                        apkFile.sink().buffer().use { sink ->
                            val buf = okio.Buffer()
                            while (true) {
                                val read = source.read(buf, 65_536)
                                if (read == -1L) break
                                sink.write(buf, read)
                                written += read
                                if (total != null) {
                                    _updateStatus.update { UpdateStatus.Downloading(written.toFloat() / total) }
                                }
                            }
                        }
                    }
                    apkFile
                }
            }.onSuccess { apkFile ->
                Log.d(TAG, "APK downloaded to ${apkFile.absolutePath}")
                val uri = FileProvider.getUriForFile(
                    appContext, "${appContext.packageName}.provider", apkFile
                )
                appContext.startActivity(
                    Intent(Intent.ACTION_VIEW)
                        .setDataAndType(uri, "application/vnd.android.package-archive")
                        .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                )
                _updateStatus.value = UpdateStatus.ReadyToInstall
            }.onFailure { e ->
                Log.e(TAG, "Download failed", e)
                _updateStatus.value = UpdateStatus.Error(e.message ?: "Download failed")
            }
        }
    }

    fun dismissError() {
        _updateStatus.value = UpdateStatus.Idle
    }
}

private fun parseVersion(tag: String): Triple<Int, Int, Int>? {
    val m = Regex("""android-v(\d+)\.(\d+)\.(\d+)""").find(tag) ?: return null
    return Triple(m.groupValues[1].toInt(), m.groupValues[2].toInt(), m.groupValues[3].toInt())
}

private fun isNewer(remote: String, current: String): Boolean {
    if (!current.startsWith("android-v")) return true
    val r = parseVersion(remote) ?: return false
    val c = parseVersion(current) ?: return true
    if (r.first != c.first) return r.first > c.first
    if (r.second != c.second) return r.second > c.second
    return r.third > c.third
}