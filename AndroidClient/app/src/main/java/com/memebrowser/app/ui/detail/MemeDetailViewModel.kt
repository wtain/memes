package com.memebrowser.app.ui.detail

import android.content.Context
import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.memebrowser.app.data.model.Meme
import com.memebrowser.app.data.repository.EnvironmentRepository
import com.memebrowser.app.data.repository.MemeRepository
import com.memebrowser.app.util.detectMimeType
import com.memebrowser.app.util.saveImageToGallery
import com.memebrowser.app.util.shareImage
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

data class DetailUiState(
    val meme: Meme? = null,
    val isLoading: Boolean = false,
    val isSaving: Boolean = false,
    val error: String? = null,
    val saveSuccess: Boolean = false,
    val similarMemes: List<Meme> = emptyList(),
    val isLoadingSimilar: Boolean = false
)

@HiltViewModel
class MemeDetailViewModel @Inject constructor(
    savedStateHandle: SavedStateHandle,
    private val repo: MemeRepository,
    private val envRepo: EnvironmentRepository
) : ViewModel() {

    private val memeId: String = checkNotNull(savedStateHandle["memeId"])

    private val _state = MutableStateFlow(DetailUiState())
    val state: StateFlow<DetailUiState> = _state.asStateFlow()

    init {
        loadMeme()
        loadSimilar()
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
                .onSuccess { memes -> _state.update { it.copy(similarMemes = memes, isLoadingSimilar = false) } }
                .onFailure { _state.update { it.copy(isLoadingSimilar = false) } }
        }
    }

    fun toggleExcluded() {
        val meme = _state.value.meme ?: return
        val currentlyExcluded = meme.excluded == true
        // Optimistic update
        _state.update { it.copy(meme = meme.copy(excluded = !currentlyExcluded)) }
        viewModelScope.launch {
            val result = if (currentlyExcluded) repo.unmarkExcluded(meme.id) else repo.markExcluded(meme.id)
            result.onFailure { e ->
                // Roll back
                _state.update { it.copy(meme = meme.copy(excluded = currentlyExcluded), error = e.message) }
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
                        val bytes = body.bytes()
                        val contentType = body.contentType()?.toString()
                        val (mimeType, ext) = detectMimeType(contentType)
                        val fileName = meme.originalFileName ?: "${meme.id}.$ext"
                        saveImageToGallery(context, bytes, fileName, mimeType, collection)
                        _state.update { it.copy(isSaving = false, saveSuccess = true) }
                    } catch (e: Exception) {
                        _state.update { it.copy(isSaving = false, error = "Save failed: ${e.message}") }
                    }
                }
                .onFailure { e -> _state.update { it.copy(isSaving = false, error = e.message) } }
        }
    }

    fun share(context: Context) {
        val meme = _state.value.meme ?: return
        viewModelScope.launch {
            repo.downloadImage(meme.id)
                .onSuccess { body ->
                    try {
                        val bytes = body.bytes()
                        val contentType = body.contentType()?.toString()
                        val (mimeType, ext) = detectMimeType(contentType)
                        shareImage(context, bytes, meme.id, ext, mimeType)
                    } catch (e: Exception) {
                        _state.update { it.copy(error = "Share failed: ${e.message}") }
                    }
                }
                .onFailure { e -> _state.update { it.copy(error = e.message) } }
        }
    }

    fun dismissError() = _state.update { it.copy(error = null) }
    fun dismissSaveSuccess() = _state.update { it.copy(saveSuccess = false) }
}