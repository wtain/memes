package com.memebrowser.app.ui.sync

import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Folder
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Replay
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material.icons.filled.Sync
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Slider
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LifecycleEventEffect
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.memebrowser.app.data.album.ScanState
import com.memebrowser.app.data.sync.SyncState
import com.memebrowser.app.data.sync.isActive
import com.memebrowser.app.data.tracker.TrackerSettingsStore

/** The callbacks of the stateless [SyncContent]; the stateful [SyncScreen] wires them to the ViewModel. */
class SyncActions(
    val onBack: () -> Unit = {},
    val onChooseAlbum: () -> Unit = {},
    val onUploadFiles: () -> Unit = {},
    val onRefresh: () -> Unit = {},
    val onSyncNow: () -> Unit = {},
    val onStop: () -> Unit = {},
    val onDismissResult: () -> Unit = {},
    val onBatchSizeChange: (Int) -> Unit = {},
    val onShowRejected: () -> Unit = {},
    val onHideRejected: () -> Unit = {},
    val onRetryAll: () -> Unit = {},
    val onRetryOne: (RejectedItem) -> Unit = {},
    val onConfirmChange: () -> Unit = {},
    val onDismissChange: () -> Unit = {},
    val onMessageShown: () -> Unit = {},
)

@Composable
fun SyncScreen(
    onBack: () -> Unit,
    onUploadFiles: () -> Unit,
    viewModel: SyncViewModel = hiltViewModel(),
) {
    val state by viewModel.uiState.collectAsStateWithLifecycle()

    val picker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocumentTree()) { uri ->
        if (uri != null) viewModel.onAlbumPicked(uri.toString())
    }

    // Refresh when the screen opens or the app comes back (rotation and double starts are throttled in the ViewModel),
    // and again when the selected environment changes.
    LifecycleEventEffect(Lifecycle.Event.ON_START) { viewModel.onScreenStarted() }
    LaunchedEffect(state.environment?.id) { if (state.environment != null) viewModel.onScreenStarted() }

    // The engine dies with the process, so the screen stays on while a run is active (foreground-only sync).
    val running = state.sync.isActive
    val view = LocalView.current
    DisposableEffect(running) {
        view.keepScreenOn = running
        onDispose { view.keepScreenOn = false }
    }

    val actions = SyncActions(
        onBack = onBack,
        onChooseAlbum = { picker.launch(null) },
        onUploadFiles = onUploadFiles,
        onRefresh = viewModel::refresh,
        onSyncNow = viewModel::startSync,
        onStop = viewModel::stopSync,
        onDismissResult = viewModel::dismissSyncResult,
        onBatchSizeChange = viewModel::setBatchSize,
        onShowRejected = viewModel::showRejected,
        onHideRejected = viewModel::hideRejected,
        onRetryAll = viewModel::retryServerErrors,
        onRetryOne = viewModel::retryServerError,
        onConfirmChange = viewModel::confirmChangeAlbum,
        onDismissChange = viewModel::dismissChangeAlbum,
        onMessageShown = viewModel::dismissMessage,
    )
    SyncContent(state, actions)
}

/** Stateless: renders [state] and reports intents through [actions]. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SyncContent(state: SyncUiState, actions: SyncActions) {
    val snackbarHostState = remember { SnackbarHostState() }
    LaunchedEffect(state.message) {
        state.message?.let {
            snackbarHostState.showSnackbar(it)
            actions.onMessageShown()
        }
    }
    BackHandler(enabled = state.rejectedVisible) { actions.onHideRejected() }

    Scaffold(
        snackbarHost = { SnackbarHost(snackbarHostState) },
        topBar = {
            TopAppBar(
                title = { Text(if (state.rejectedVisible) "Rejected files" else "Album sync") },
                navigationIcon = {
                    IconButton(onClick = if (state.rejectedVisible) actions.onHideRejected else actions.onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
                    }
                },
            )
        },
    ) { padding ->
        Box(Modifier.fillMaxSize().padding(padding)) {
            when {
                !state.loaded -> CircularProgressIndicator(Modifier.align(Alignment.Center))
                state.rejectedVisible -> RejectedList(state, actions)
                else -> Column(
                    Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
                    verticalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    Header(state)
                    if (state.album == null) EmptyState(state, actions) else AlbumSections(state, actions)
                    OutlinedButton(
                        onClick = actions.onUploadFiles,
                        modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp),
                    ) { Text("Upload individual files...") }
                }
            }
        }
    }

    state.changePlan?.let { plan ->
        ChangeAlbumDialog(
            lines = changeAlbumDialogLines(plan, syncRunning = state.sync.isActive),
            busy = state.busy,
            onConfirm = actions.onConfirmChange,
            onDismiss = actions.onDismissChange,
        )
    }
}

@Composable
private fun Header(state: SyncUiState) {
    Column(Modifier.fillMaxWidth()) {
        Text(
            "Environment: ${state.environment?.name.orEmpty()}",
            style = MaterialTheme.typography.titleMedium,
            modifier = Modifier.testTag("sync_environment"),
        )
        state.album?.let {
            Text(
                "Album: ${it.displayName.ifBlank { "(unnamed folder)" }}",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.testTag("sync_album"),
            )
        }
    }
}

@Composable
private fun EmptyState(state: SyncUiState, actions: SyncActions) {
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("No album chosen for this environment", style = MaterialTheme.typography.titleMedium)
            Text(
                "Choose the folder on this phone whose images should be sent to the server. The choice is remembered for this environment.",
                style = MaterialTheme.typography.bodyMedium,
            )
            Button(
                onClick = actions.onChooseAlbum,
                enabled = state.controls.canChooseAlbum,
                modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp),
            ) {
                Icon(Icons.Default.Folder, contentDescription = null, modifier = Modifier.size(20.dp))
                Text("  Choose album folder")
            }
        }
    }
}

@Composable
private fun AlbumSections(state: SyncUiState, actions: SyncActions) {
    val controls = state.controls
    StatusCard(state, actions)
    if (state.scan == ScanState.PermissionLost) {
        Card(Modifier.fillMaxWidth()) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                ProblemText("Access to the album was lost.")
                Text("Your upload list is kept. Choose the same folder again to continue.", style = MaterialTheme.typography.bodyMedium)
                Button(onClick = actions.onChooseAlbum, enabled = !state.busy, modifier = Modifier.heightIn(min = 48.dp)) {
                    Text("Choose album again")
                }
            }
        }
    }
    SyncCard(state, actions)
    BatchSizeRow(state, actions, controls)
    HorizontalDivider()
    OutlinedButton(
        onClick = actions.onChooseAlbum,
        enabled = controls.canChangeAlbum,
        modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp),
    ) {
        Icon(Icons.Default.Folder, contentDescription = null, modifier = Modifier.size(20.dp))
        Text("  Change album")
    }
}

@Composable
private fun StatusCard(state: SyncUiState, actions: SyncActions) {
    val counts = state.counts
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(
                "${counts.uploaded} of ${counts.supported} uploaded",
                style = MaterialTheme.typography.headlineSmall,
                modifier = Modifier.testTag("sync_progress_text"),
            )
            LinearProgressIndicator(
                progress = { progressFraction(counts) },
                modifier = Modifier.fillMaxWidth().semantics {
                    contentDescription = "${counts.uploaded} of ${counts.supported} files uploaded"
                },
            )
            Row(horizontalArrangement = Arrangement.spacedBy(16.dp), verticalAlignment = Alignment.CenterVertically) {
                Text("${counts.pending} pending", style = MaterialTheme.typography.bodyMedium, modifier = Modifier.testTag("sync_pending"))
                TextButton(onClick = actions.onShowRejected, modifier = Modifier.heightIn(min = 48.dp)) {
                    Text("${counts.rejected} rejected", modifier = Modifier.testTag("sync_rejected"))
                }
            }
            scanStatusText(state.scan)?.let { text ->
                if (scanIsProblem(state.scan)) ProblemText(text) else {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        if (state.scan == ScanState.Scanning) {
                            CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp)
                            Spacer(Modifier.size(8.dp))
                        }
                        Text(text, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            }
            OutlinedButton(
                onClick = actions.onRefresh,
                enabled = state.controls.canRefresh,
                modifier = Modifier.heightIn(min = 48.dp),
            ) {
                Icon(Icons.Default.Refresh, contentDescription = null, modifier = Modifier.size(20.dp))
                Text("  Refresh")
            }
        }
    }
}

@Composable
private fun SyncCard(state: SyncUiState, actions: SyncActions) {
    val controls = state.controls
    val sync = state.sync
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            if (sync.isActive) {
                Button(
                    onClick = actions.onStop,
                    enabled = controls.canStop,
                    modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp),
                ) {
                    Icon(Icons.Default.Stop, contentDescription = null, modifier = Modifier.size(20.dp))
                    Text("  Stop")
                }
                Text("Keep the app open while syncing", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            } else {
                Button(
                    onClick = actions.onSyncNow,
                    enabled = controls.canSync,
                    modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp),
                ) {
                    Icon(Icons.Default.Sync, contentDescription = null, modifier = Modifier.size(20.dp))
                    Text("  Sync now")
                }
                if (state.counts.pending == 0 && sync == SyncState.Idle) {
                    Text("Nothing to send.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            syncStatusText(sync)?.let { text ->
                Row(verticalAlignment = Alignment.Top) {
                    Column(Modifier.weight(1f).testTag("sync_status")) {
                        if (sync is SyncState.Failed) ProblemText(text)
                        else Text(text, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.Medium)
                    }
                    if (sync.isTerminal()) {
                        IconButton(onClick = actions.onDismissResult, modifier = Modifier.size(48.dp)) {
                            Icon(Icons.Default.Close, contentDescription = "Dismiss message")
                        }
                    }
                }
            }
            if (sync is SyncState.Failed && syncErrorNeedsRepick(sync.error)) {
                Button(onClick = actions.onChooseAlbum, modifier = Modifier.heightIn(min = 48.dp)) { Text("Choose album again") }
            }
        }
    }
}

@Composable
private fun BatchSizeRow(state: SyncUiState, actions: SyncActions, controls: SyncControls) {
    var draft by remember(state.batchSize) { mutableFloatStateOf(state.batchSize.toFloat()) }
    Column(Modifier.fillMaxWidth()) {
        Text("Batch size: ${draft.toInt()} files per request", style = MaterialTheme.typography.bodyMedium)
        Slider(
            value = draft,
            onValueChange = { draft = it },
            onValueChangeFinished = { actions.onBatchSizeChange(draft.toInt()) },
            valueRange = TrackerSettingsStore.MIN_BATCH_SIZE.toFloat()..TrackerSettingsStore.MAX_BATCH_SIZE.toFloat(),
            steps = TrackerSettingsStore.MAX_BATCH_SIZE - TrackerSettingsStore.MIN_BATCH_SIZE - 1,
            modifier = Modifier.semantics { contentDescription = "Batch size, ${draft.toInt()} files, from 1 to 50" },
        )
        if (controls.batchSizeAppliesNextBatch) {
            Text(
                "A change applies from the next batch.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@Composable
private fun RejectedList(state: SyncUiState, actions: SyncActions) {
    Column(Modifier.fillMaxSize()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text(
                "Rejected files stay skipped. Only files the server failed on can be retried.",
                style = MaterialTheme.typography.bodyMedium,
            )
            if (state.retryableCount > 0) {
                Button(onClick = actions.onRetryAll, modifier = Modifier.heightIn(min = 48.dp)) {
                    Icon(Icons.Default.Replay, contentDescription = null, modifier = Modifier.size(20.dp))
                    Text("  Retry all server errors (${state.retryableCount})")
                }
            }
        }
        if (state.rejected.isEmpty()) {
            Text("No rejected files.", Modifier.padding(16.dp), style = MaterialTheme.typography.bodyMedium)
        }
        LazyColumn(Modifier.fillMaxSize()) {
            items(state.rejected, key = { it.albumTreeUri + "|" + it.documentId }) { item ->
                Row(
                    Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Column(Modifier.weight(1f)) {
                        Text(item.displayName, style = MaterialTheme.typography.bodyMedium, maxLines = 2)
                        Text(item.reasonText, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    if (item.canRetry) {
                        TextButton(onClick = { actions.onRetryOne(item) }, modifier = Modifier.heightIn(min = 48.dp)) { Text("Retry") }
                    }
                }
                HorizontalDivider()
            }
        }
    }
}

@Composable
private fun ChangeAlbumDialog(lines: List<String>, busy: Boolean, onConfirm: () -> Unit, onDismiss: () -> Unit) {
    AlertDialog(
        onDismissRequest = { if (!busy) onDismiss() },
        title = { Text("Change album?") },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                lines.forEach { line ->
                    if (line.startsWith(WARNING_PREFIX)) ProblemText(line) else Text(line, style = MaterialTheme.typography.bodyMedium)
                }
            }
        },
        confirmButton = {
            TextButton(onClick = onConfirm, enabled = !busy, modifier = Modifier.heightIn(min = 48.dp)) {
                Text("Change and reset")
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss, enabled = !busy, modifier = Modifier.heightIn(min = 48.dp)) { Text("Cancel") }
        },
    )
}

/** Error-coloured text with a warning icon, so a problem is never shown by colour alone. */
@Composable
private fun ProblemText(text: String) {
    Row(verticalAlignment = Alignment.Top) {
        Icon(
            Icons.Default.Warning,
            contentDescription = "Problem",
            tint = MaterialTheme.colorScheme.error,
            modifier = Modifier.size(20.dp),
        )
        Spacer(Modifier.size(8.dp))
        Text(text, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.error)
    }
}
