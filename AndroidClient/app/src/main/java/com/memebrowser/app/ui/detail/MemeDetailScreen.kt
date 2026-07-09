package com.memebrowser.app.ui.detail

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutVertically
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.material3.SuggestionChip
import androidx.compose.material3.SuggestionChipDefaults
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Block
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Download
import androidx.compose.material.icons.filled.Share
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import coil.compose.AsyncImage
import com.memebrowser.app.data.model.Meme
import com.memebrowser.app.data.model.MemeTag
import me.saket.telephoto.zoomable.coil.ZoomableAsyncImage

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MemeDetailScreen(
    memeId: String,
    onBack: () -> Unit,
    onNavigateToMeme: (String) -> Unit,
    onTagClick: (category: String, value: String) -> Unit = { _, _ -> },
    viewModel: MemeDetailViewModel = hiltViewModel()
) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    val context = LocalContext.current
    val snackbarHostState = remember { SnackbarHostState() }
    var toolbarVisible by remember { mutableStateOf(true) }

    LaunchedEffect(state.error) {
        if (state.error != null) {
            snackbarHostState.showSnackbar(state.error!!)
            viewModel.dismissError()
        }
    }
    LaunchedEffect(state.saveSuccess) {
        if (state.saveSuccess) {
            snackbarHostState.showSnackbar("Saved to MemesBrowser gallery")
            viewModel.dismissSaveSuccess()
        }
    }

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(Color.Black)
    ) {
        when {
            state.isLoading -> CircularProgressIndicator(modifier = Modifier.align(Alignment.Center))
            state.meme != null -> ZoomableAsyncImage(
                model = "http://localhost${state.meme!!.imageUrl}",
                contentDescription = state.meme!!.originalFileName,
                modifier = Modifier.fillMaxSize(),
                onClick = { toolbarVisible = !toolbarVisible }
            )
        }

        AnimatedVisibility(
            visible = toolbarVisible,
            modifier = Modifier.align(Alignment.TopStart),
            enter = fadeIn(),
            exit = fadeOut()
        ) {
            TopAppBar(
                title = {},
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(containerColor = Color.Black.copy(alpha = 0.5f)),
            )
        }

        AnimatedVisibility(
            visible = toolbarVisible && state.meme != null,
            modifier = Modifier.align(Alignment.BottomCenter),
            enter = slideInVertically { it } + fadeIn(),
            exit = slideOutVertically { it } + fadeOut()
        ) {
            state.meme?.let { meme ->
                BottomActionBar(
                    meme = meme,
                    isSaving = state.isSaving,
                    onSave = { viewModel.saveToGallery(context) },
                    onShare = { viewModel.share(context) },
                    onToggleFlagged = { viewModel.toggleFlagged() },
                    similarMemes = state.similarMemes,
                    isLoadingSimilar = state.isLoadingSimilar,
                    onSimilarMemeClick = onNavigateToMeme,
                    onTagClick = onTagClick
                )
            }
        }

        SnackbarHost(
            hostState = snackbarHostState,
            modifier = Modifier
                .align(Alignment.BottomCenter)
                .navigationBarsPadding()
        )
    }
}

@Composable
private fun BottomActionBar(
    meme: Meme,
    isSaving: Boolean,
    onSave: () -> Unit,
    onShare: () -> Unit,
    onToggleFlagged: () -> Unit,
    similarMemes: List<Meme>,
    isLoadingSimilar: Boolean,
    onSimilarMemeClick: (String) -> Unit,
    onTagClick: (category: String, value: String) -> Unit
) {
    val visibleTags = remember(meme.tags) {
        meme.tags.orEmpty().filter { it.score == null || it.score > 0.3f }
    }

    Column(
        modifier = Modifier
            .fillMaxWidth()
            .background(Color.Black.copy(alpha = 0.6f))
            .navigationBarsPadding()
            .padding(vertical = 4.dp)
    ) {
        if (isLoadingSimilar || similarMemes.isNotEmpty()) {
            LazyRow(
                modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
                horizontalArrangement = Arrangement.spacedBy(4.dp)
            ) {
                if (isLoadingSimilar) {
                    item { CircularProgressIndicator(modifier = Modifier.size(24.dp)) }
                } else {
                    items(similarMemes, key = { it.id }) { similar ->
                        AsyncImage(
                            model = "http://localhost${similar.imageUrl}",
                            contentDescription = similar.originalFileName,
                            contentScale = ContentScale.Crop,
                            modifier = Modifier
                                .size(80.dp)
                                .clip(RoundedCornerShape(4.dp))
                                .clickable { onSimilarMemeClick(similar.id) }
                        )
                    }
                }
            }
        }

        if (visibleTags.isNotEmpty()) {
            TagsRow(tags = visibleTags, onTagClick = onTagClick)
        }

        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceEvenly
        ) {
            IconButton(onClick = onSave, enabled = !isSaving) {
                if (isSaving) {
                    CircularProgressIndicator(modifier = Modifier.size(24.dp))
                } else {
                    Icon(Icons.Default.Download, contentDescription = "Save to gallery", tint = Color.White)
                }
            }
            IconButton(onClick = onShare) {
                Icon(Icons.Default.Share, contentDescription = "Share", tint = Color.White)
            }
            IconButton(onClick = onToggleFlagged) {
                if (meme.flagged == true) {
                    Icon(
                        Icons.Default.CheckCircle,
                        contentDescription = "Unmark flagged",
                        tint = MaterialTheme.colorScheme.error
                    )
                } else {
                    Icon(Icons.Default.Block, contentDescription = "Mark flagged", tint = Color.White)
                }
            }
        }
    }
}

@Composable
private fun TagsRow(
    tags: List<MemeTag>,
    onTagClick: (category: String, value: String) -> Unit
) {
    LazyRow(
        modifier = Modifier.padding(horizontal = 8.dp, vertical = 2.dp),
        horizontalArrangement = Arrangement.spacedBy(6.dp)
    ) {
        itemsIndexed(tags, key = { i, tag -> "${i}:${tag.category}:${tag.name}" }) { _, tag ->
            SuggestionChip(
                onClick = { onTagClick(tag.category ?: "tag", tag.name) },
                label = {
                    Text(
                        text = if (tag.source != null) "${tag.name} (${tag.source})" else tag.name,
                        style = MaterialTheme.typography.labelSmall
                    )
                },
                colors = SuggestionChipDefaults.suggestionChipColors(
                    containerColor = Color.White.copy(alpha = 0.15f),
                    labelColor = Color.White
                ),
                border = SuggestionChipDefaults.suggestionChipBorder(
                    enabled = true,
                    borderColor = Color.White.copy(alpha = 0.3f)
                )
            )
        }
    }
}