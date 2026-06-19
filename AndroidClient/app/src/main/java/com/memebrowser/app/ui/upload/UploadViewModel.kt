package com.memebrowser.app.ui.upload

import android.content.Context
import android.net.Uri
import android.provider.OpenableColumns
import android.util.Log
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.memebrowser.app.data.model.UploadResponse
import com.memebrowser.app.data.repository.MemeRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.RequestBody
import okio.BufferedSink
import okio.source
import javax.inject.Inject

private const val TAG = "UploadViewModel"
private const val MAX_FILES = 50
private const val MAX_FILE_SIZE = 20 * 1024 * 1024L
private val ALLOWED_MIME_TYPES = setOf(
    "image/jpeg", "image/png", "image/gif", "image/webp", "image/bmp", "image/tiff"
)

data class UploadItem(
    val uri: Uri,
    val name: String,
    val size: Long,
    val mimeType: String
) {
    val isValidType: Boolean get() = mimeType in ALLOWED_MIME_TYPES
    val isValidSize: Boolean get() = size <= MAX_FILE_SIZE
    val isValid: Boolean get() = isValidType && isValidSize
}

data class UploadUiState(
    val items: List<UploadItem> = emptyList(),
    val isUploading: Boolean = false,
    val result: UploadResponse? = null,
    val error: String? = null
)

@HiltViewModel
class UploadViewModel @Inject constructor(
    @ApplicationContext private val appContext: Context,
    private val repo: MemeRepository
) : ViewModel() {

    private val _state = MutableStateFlow(UploadUiState())
    val state: StateFlow<UploadUiState> = _state.asStateFlow()

    fun addUris(uris: List<Uri>) {
        val resolver = appContext.contentResolver
        val newItems = uris.mapNotNull { uri ->
            val mimeType = resolver.getType(uri) ?: return@mapNotNull null
            resolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE), null, null, null)
                ?.use { cursor ->
                    if (!cursor.moveToFirst()) return@mapNotNull null
                    val name = cursor.getString(cursor.getColumnIndexOrThrow(OpenableColumns.DISPLAY_NAME))
                    val size = cursor.getLong(cursor.getColumnIndexOrThrow(OpenableColumns.SIZE))
                    UploadItem(uri, name, size, mimeType)
                }
        }
        _state.update { current ->
            val combined = (current.items + newItems).distinctBy { it.uri }.take(MAX_FILES)
            current.copy(items = combined, result = null, error = null)
        }
    }

    fun removeItem(item: UploadItem) {
        _state.update { it.copy(items = it.items - item, result = null) }
    }

    fun clearAll() {
        _state.value = UploadUiState()
    }

    fun upload() {
        val validItems = _state.value.items.filter { it.isValid }
        if (validItems.isEmpty()) return
        viewModelScope.launch {
            _state.update { it.copy(isUploading = true, error = null, result = null) }
            val resolver = appContext.contentResolver
            val parts = validItems.mapNotNull { item ->
                try {
                    val mediaType = item.mimeType.toMediaType()
                    val requestBody = object : RequestBody() {
                        override fun contentType() = mediaType
                        override fun writeTo(sink: BufferedSink) {
                            resolver.openInputStream(item.uri)?.use { stream ->
                                sink.writeAll(stream.source())
                            }
                        }
                    }
                    MultipartBody.Part.createFormData("files", item.name, requestBody)
                } catch (e: Exception) {
                    Log.e(TAG, "Failed to prepare part for ${item.name}", e)
                    null
                }
            }
            repo.uploadImages(parts)
                .onSuccess { response ->
                    Log.d(TAG, "Upload ok: accepted=${response.totalAccepted} failed=${response.totalFailed}")
                    _state.update { it.copy(isUploading = false, result = response, items = emptyList()) }
                }
                .onFailure { e ->
                    Log.e(TAG, "Upload failed", e)
                    _state.update { it.copy(isUploading = false, error = e.message ?: "Upload failed") }
                }
        }
    }

    fun dismissError() = _state.update { it.copy(error = null) }
}