package com.memebrowser.app.ui.detail

import android.content.Context
import android.util.Log
import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.memebrowser.app.data.model.Meme
import com.memebrowser.app.data.model.ImageDescription
import com.memebrowser.app.data.repository.EnvironmentRepository
import com.memebrowser.app.data.repository.MemeRepository
import com.memebrowser.app.util.detectMimeType
import com.memebrowser.app.util.saveImageToGallery
import com.memebrowser.app.util.shareImage
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import javax.inject.Inject

data class DetailUiState(
    val meme: Meme? = null,
    val isLoading: Boolean = false,
    val isSaving: Boolean = false,
    val error: String? = null,
    val saveSuccess: Boolean = false,
    val similarMemes: List<Meme> = emptyList(),
    val isLoadingSimilar: Boolean = false,
    val descriptions: List<ImageDescription> = emptyList()
)

@HiltViewModel
class MemeDetailViewModel @Inject constructor(
    savedStateHandle: SavedStateHandle,
    private val repo: MemeRepository,
    private val envRepo: EnvironmentRepository
) : ViewModel() {
    companion object {
        private const val TAG = "MemeDetailViewModel"
    }


    private val memeId: String = checkNotNull(savedStateHandle["memeId"])

    private val _state = MutableStateFlow(DetailUiState())
    val state: StateFlow<DetailUiState> = _state.asStateFlow()

    init {
        loadMeme()
        loadSimilar()
        loadDescriptions()
    }

    private fun loadMeme() {
        viewModelScope.launch {
            _state.update { it.copy(isLoading = true, error = null) }
            repo.getMeme(memeId)
                .onSuccess { meme -> _state.update { it.copy(meme = meme, isLoading = false) } }
                .onFailure { e -> _state.update { it.copy(error = e.message, isLoading = false) } }
        }
    }

    private fun loadSimilar() {
        viewModelScope.launch {
            _state.update { it.copy(isLoadingSimilar = true) }
            repo.getSimilarMemes(memeId)
                .onSuccess { memes ->
                    val deduped = memes.filter { it.id != memeId }.distinctBy { it.id }
                    _state.update { it.copy(similarMemes = deduped, isLoadingSimilar = false) }
                }
                .onFailure { _state.update { it.copy(isLoadingSimilar = false) } }
        }
    }

    private fun loadDescriptions() {
        viewModelScope.launch {
            repo.getDescriptions(memeId)
                .onSuccess { descriptions -> _state.update { it.copy(descriptions = descriptions) } }
                .onFailure { /* silent — supplementary content, matches loadSimilar's failure handling */ }
        }
    }

    fun toggleFlagged() {
        val meme = _state.value.meme ?: return
        val currentlyFlagged = meme.flagged == true
        // Optimistic update
        _state.update { it.copy(meme = meme.copy(flagged = !currentlyFlagged)) }
        viewModelScope.launch {
            val result = if (currentlyFlagged) repo.unmarkFlagged(meme.id) else repo.markFlagged(meme.id)
            result.onFailure { e ->
                // Roll back
                _state.update { it.copy(meme = meme.copy(flagged = currentlyFlagged), error = e.message) }
            }
        }
    }

    fun saveToGallery(context: Context) {
        val meme = _state.value.meme ?: return
        viewModelScope.launch {
            _state.update { it.copy(isSaving = true, error = null, saveSuccess = false) }
            val collection = envRepo.selectedEnvironmentName.first()
            repo.downloadImage(meme.id)
                .onSuccess { body ->
                    try {
                        val contentType = body.contentType()?.toString()
                        val contentLength = body.contentLength()
                        Log.d(TAG, "saveToGallery: reading body id=${meme.id} contentType=$contentType contentLength=$contentLength")
                        val bytes = withContext(Dispatchers.IO) { body.bytes() }
                        Log.d(TAG, "saveToGallery: read ${bytes.size} bytes for id=${meme.id}")
                        val (mimeType, ext) = detectMimeType(contentType)
                        val fileName = meme.originalFileName ?: "${meme.id}.$ext"
                        saveImageToGallery(context, bytes, fileName, mimeType, collection)
                        _state.update { it.copy(isSaving = false, saveSuccess = true) }
                    } catch (e: Exception) {
                        Log.e(TAG, "saveToGallery: body read/write failed id=${meme.id}", e)
                        _state.update { it.copy(isSaving = false, error = "Save failed: ${e.message}") }
                    }
                }
                .onFailure { e ->
                    Log.e(TAG, "saveToGallery: download failed id=${meme.id}", e)
                    _state.update { it.copy(isSaving = false, error = "Download failed: ${e.message}") }
                }
        }
    }

    fun share(context: Context) {
        val meme = _state.value.meme ?: return
        viewModelScope.launch {
            repo.downloadImage(meme.id)
                .onSuccess { body ->
                    try {
                        val contentType = body.contentType()?.toString()
                        Log.d(TAG, "share: reading body id=${meme.id} contentType=$contentType contentLength=${body.contentLength()}")
                        val bytes = withContext(Dispatchers.IO) { body.bytes() }
                        val (mimeType, ext) = detectMimeType(contentType)
                        shareImage(context, bytes, meme.id, ext, mimeType)
                    } catch (e: Exception) {
                        Log.e(TAG, "share: body read/send failed id=${meme.id}", e)
                        _state.update { it.copy(error = "Share failed: ${e.message}") }
                    }
                }
                .onFailure { e ->
                    Log.e(TAG, "share: download failed id=${meme.id}", e)
                    _state.update { it.copy(error = "Download failed: ${e.message}") }
                }
        }
    }

    fun dismissError() = _state.update { it.copy(error = null) }
    fun dismissSaveSuccess() = _state.update { it.copy(saveSuccess = false) }
}