package com.memebrowser.app.ui.detail

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Block
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Download
import androidx.compose.material.icons.filled.Share
import androidx.compose.material3.AssistChip
import androidx.compose.material3.BottomSheetScaffold
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
import androidx.compose.material3.rememberBottomSheetScaffoldState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.memebrowser.app.data.model.Meme
import me.saket.telephoto.zoomable.coil.ZoomableAsyncImage

@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
fun MemeDetailScreen(
    memeId: String,
    onBack: () -> Unit,
    viewModel: MemeDetailViewModel = hiltViewModel()
) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    val context = LocalContext.current
    val snackbarHostState = remember { SnackbarHostState() }
    val scaffoldState = rememberBottomSheetScaffoldState()

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

    BottomSheetScaffold(
        scaffoldState = scaffoldState,
        snackbarHost = { SnackbarHost(snackbarHostState) },
        sheetPeekHeight = 80.dp,
        sheetContent = {
            state.meme?.let { meme ->
                MemeActionsAndMetadata(
                    meme = meme,
                    isSaving = state.isSaving,
                    onSave = { viewModel.saveToGallery(context) },
                    onShare = { viewModel.share(context) },
                    onToggleExcluded = { viewModel.toggleExcluded() }
                )
            }
        }
    ) { paddingValues ->
        Box(
            modifier = Modifier
                .fillMaxSize()
                .background(Color.Black)
                .padding(paddingValues)
        ) {
            when {
                state.isLoading -> CircularProgressIndicator(modifier = Modifier.align(Alignment.Center))
                state.meme != null -> ZoomableAsyncImage(
                    model = "http://localhost${state.meme!!.imageUrl}",
                    contentDescription = state.meme!!.originalFileName,
                    modifier = Modifier.fillMaxSize()
                )
            }
            TopAppBar(
                title = {},
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(containerColor = Color.Transparent),
                modifier = Modifier.align(Alignment.TopStart)
            )
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun MemeActionsAndMetadata(
    meme: Meme,
    isSaving: Boolean,
    onSave: () -> Unit,
    onShare: () -> Unit,
    onToggleExcluded: () -> Unit
) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .navigationBarsPadding()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 16.dp, vertical = 8.dp)
    ) {
        // Action row
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceEvenly
        ) {
            IconButton(onClick = onSave, enabled = !isSaving) {
                if (isSaving) {
                    CircularProgressIndicator(modifier = Modifier.size(24.dp))
                } else {
                    Icon(Icons.Default.Download, contentDescription = "Save to gallery")
                }
            }
            IconButton(onClick = onShare) {
                Icon(Icons.Default.Share, contentDescription = "Share")
            }
            IconButton(onClick = onToggleExcluded) {
                if (meme.excluded == true) {
                    Icon(
                        Icons.Default.CheckCircle,
                        contentDescription = "Unmark excluded",
                        tint = MaterialTheme.colorScheme.error
                    )
                } else {
                    Icon(Icons.Default.Block, contentDescription = "Mark excluded")
                }
            }
        }

        // Metadata
        meme.originalFileName?.let {
            Spacer(Modifier.height(8.dp))
            Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }

        if (!meme.text.isNullOrEmpty()) {
            Spacer(Modifier.height(12.dp))
            Text("Text", style = MaterialTheme.typography.labelMedium)
            Spacer(Modifier.height(4.dp))
            meme.text.forEach { line ->
                Text(line, style = MaterialTheme.typography.bodyMedium)
            }
        }

        if (!meme.tags.isNullOrEmpty()) {
            Spacer(Modifier.height(12.dp))
            Text("Tags", style = MaterialTheme.typography.labelMedium)
            Spacer(Modifier.height(4.dp))
            val grouped = meme.tags.groupBy { it.category ?: "other" }
            grouped.forEach { (category, tags) ->
                Text(category, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.primary)
                FlowRow(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                    tags.forEach { tag ->
                        AssistChip(
                            onClick = {},
                            label = { Text(tag.name, style = MaterialTheme.typography.bodySmall) }
                        )
                    }
                }
            }
        }

        Spacer(Modifier.height(16.dp))
    }
}