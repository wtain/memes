package com.memebrowser.app.ui.sync

import android.app.Activity
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.ContextWrapper
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import android.util.Log
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.IntentSenderRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Folder
import androidx.compose.material.icons.filled.Menu
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Replay
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material.icons.filled.Sync
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DrawerValue
import androidx.compose.material3.ModalDrawerSheet
import androidx.compose.material3.ModalNavigationDrawer
import androidx.compose.material3.rememberDrawerState
import androidx.compose.runtime.rememberCoroutineScope
import com.memebrowser.app.data.capabilities.FeatureVisibility
import com.memebrowser.app.ui.drawer.DrawerContent
import kotlinx.coroutines.launch
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
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LifecycleEventEffect
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.memebrowser.app.data.album.GalleryAlbum
import com.memebrowser.app.data.album.MediaAccess
import com.memebrowser.app.data.album.MediaAccesses
import com.memebrowser.app.data.album.ScanState
import com.memebrowser.app.data.sync.SyncState
import com.memebrowser.app.data.sync.isActive
import com.memebrowser.app.data.tracker.TrackerSettingsStore
import kotlin.math.roundToInt

/** The callbacks of the stateless [SyncContent]; the stateful [SyncScreen] wires them to the ViewModel. */
@Immutable
class SyncActions(
    /** Leaves the rejected list (the main view opens the app menu instead, see [onMenu]). */
    val onBack: () -> Unit = {},
    val onMenu: () -> Unit = {},
    /** The folder picker. */
    val onChooseAlbum: () -> Unit = {},
    val onChooseGallery: () -> Unit = {},
    val onChooserDismiss: () -> Unit = {},
    val onChooseGalleryAlbum: (GalleryAlbum) -> Unit = {},
    val onRequestGalleryPermission: () -> Unit = {},
    val onOpenAppSettings: () -> Unit = {},
    val onUploadFiles: () -> Unit = {},
    val onRefresh: () -> Unit = {},
    val onSyncNow: () -> Unit = {},
    val onStop: () -> Unit = {},
    val onDismissResult: () -> Unit = {},
    val onBatchSizeChange: (Int) -> Unit = {},
    val onRemoveSynced: () -> Unit = {},
    val onCancelRemoval: () -> Unit = {},
    val onConfirmRemoval: () -> Unit = {},
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
    /** Opens another screen from the app menu; the route is one of the NavGraph routes. */
    onNavigate: (route: String) -> Unit = {},
    visibility: FeatureVisibility = FeatureVisibility.ALL,
    viewModel: SyncViewModel = hiltViewModel(),
) {
    val state by viewModel.uiState.collectAsStateWithLifecycle()
    val drawerState = rememberDrawerState(initialValue = DrawerValue.Closed)
    val scope = rememberCoroutineScope()
    BackHandler(enabled = drawerState.isOpen) { scope.launch { drawerState.close() } }

    val picker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocumentTree()) { uri ->
        if (uri != null) {
            Log.i("SyncScreen", "Folder picker returned a tree: $uri")
            viewModel.onAlbumPicked(uri.toString())
        } else {
            Log.w("SyncScreen", "Folder picker returned no result (cancelled, or the system refused the folder)")
            viewModel.onPickerCancelled()
        }
    }

    // Refresh when the screen opens or the app comes back, and again when the selected environment changes. On the
    // first open both effects fire; the ViewModel's 30 s throttle per environment absorbs the second call.
    LifecycleEventEffect(Lifecycle.Event.ON_START) { viewModel.onScreenStarted() }
    LaunchedEffect(state.environment?.id) { if (state.environment != null) viewModel.onScreenStarted() }

    // The engine dies with the process, so the screen stays on while a run is active (foreground-only sync).
    val running = state.sync.isActive
    val view = LocalView.current
    DisposableEffect(running) {
        view.keepScreenOn = running
        onDispose { view.keepScreenOn = false }
    }

    val context = LocalContext.current
    val permissions = remember { MediaAccesses.permissionsToRequest(Build.VERSION.SDK_INT) }
    val permissionLauncher = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { result ->
        val activity = context.findActivity()
        val rationale = activity != null && permissions.any { activity.shouldShowRequestPermissionRationale(it) }
        viewModel.onPermissionResult(canAskAgain = rationale || result.values.any { it })
    }
    // The tap shows the state; a plain "denied" (the system may still ask) opens the system dialog right away.
    // One shot per entry into DENIED: the saveable flag survives rotation and a double mount, and clears when the
    // chooser leaves DENIED (dismissed, blocked, listed), so the next tap asks once more. The "Allow access" button
    // is the deliberate second ask and launches directly.
    var requestedForDenied by rememberSaveable { mutableStateOf(false) }
    LaunchedEffect(state.chooser) {
        if (state.chooser == ChooserState.NeedsPermission(MediaAccess.DENIED)) {
            if (!requestedForDenied) {
                requestedForDenied = true
                permissionLauncher.launch(permissions)
            }
        } else {
            requestedForDenied = false
        }
    }

    // The system's own "move these photos to the trash?" dialog; it acts only when the user allows it.
    val deleteLauncher = rememberLauncherForActivityResult(ActivityResultContracts.StartIntentSenderForResult()) { result ->
        viewModel.onRemovalResult(result.resultCode == Activity.RESULT_OK)
    }
    (state.removal as? RemovalState.Launch)?.let { launch ->
        LaunchedEffect(launch) {
            viewModel.onRemovalLaunched() // first: a rotation during the launch must not open a second dialog
            deleteLauncher.launch(IntentSenderRequest.Builder(launch.sender).build())
        }
    }

    val actions = remember(viewModel, picker, permissionLauncher, permissions, context, onBack, onUploadFiles, scope, drawerState) {
        SyncActions(
            onBack = onBack,
            onMenu = { scope.launch { drawerState.open() } },
            onChooseAlbum = { picker.launch(null) },
            onChooseGallery = viewModel::onGalleryRequested,
            onChooserDismiss = viewModel::dismissChooser,
            onChooseGalleryAlbum = viewModel::onGalleryAlbumChosen,
            onRequestGalleryPermission = { permissionLauncher.launch(permissions) },
            onOpenAppSettings = { context.openAppSettings() },
            onUploadFiles = onUploadFiles,
            onRefresh = viewModel::refresh,
            onSyncNow = viewModel::startSync,
            onStop = viewModel::stopSync,
            onDismissResult = viewModel::dismissSyncResult,
            onBatchSizeChange = viewModel::setBatchSize,
            onRemoveSynced = viewModel::onRemoveSyncedRequested,
            onCancelRemoval = viewModel::cancelRemoval,
            onConfirmRemoval = viewModel::confirmRemoval,
            onShowRejected = viewModel::showRejected,
            onHideRejected = viewModel::hideRejected,
            onRetryAll = viewModel::retryServerErrors,
            onRetryOne = viewModel::retryServerError,
            onConfirmChange = viewModel::confirmChangeAlbum,
            onDismissChange = viewModel::dismissChangeAlbum,
            onMessageShown = viewModel::dismissMessage,
        )
    }
    // Same app menu as the Search screen; Album sync is the current item, so it only closes the menu.
    val goTo: (String) -> Unit = { route ->
        scope.launch { drawerState.close() }
        onNavigate(route)
    }
    ModalNavigationDrawer(
        drawerState = drawerState,
        drawerContent = {
            ModalDrawerSheet {
                DrawerContent(
                    healthStatus = null,
                    visibility = visibility,
                    albumSyncSelected = true,
                    onUploadClick = { scope.launch { drawerState.close() } },
                    onFlaggedClick = { goTo("flagged") },
                    onEnvironmentsClick = { goTo("environments") },
                    onSettingsClick = { goTo("settings") },
                    onAboutClick = { goTo("about") },
                    onTrendsClick = { goTo("trends") },
                    onRecommendationsClick = { goTo("recommendations") },
                    onStatisticsClick = { goTo("statistics") },
                )
            }
        },
    ) {
        SyncContent(state, actions)
    }
}

private tailrec fun Context.findActivity(): Activity? = when (this) {
    is Activity -> this
    is ContextWrapper -> baseContext.findActivity()
    else -> null
}

private fun Context.openAppSettings() {
    val intent = Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.fromParts("package", packageName, null))
        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    try {
        startActivity(intent)
    } catch (e: ActivityNotFoundException) {
        Log.w("SyncScreen", "No activity for the app settings", e)
    }
}

/** Stateless: renders [state] and reports intents through [actions]. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SyncContent(state: SyncUiState, actions: SyncActions) {
    val snackbarHostState = remember { SnackbarHostState() }
    val onMessageShown by rememberUpdatedState(actions.onMessageShown)
    LaunchedEffect(state.message) {
        state.message?.let {
            snackbarHostState.showSnackbar(it)
            onMessageShown()
        }
    }
    BackHandler(enabled = state.rejectedVisible) { actions.onHideRejected() }

    Scaffold(
        snackbarHost = { SnackbarHost(snackbarHostState) },
        topBar = {
            TopAppBar(
                title = { Text(if (state.rejectedVisible) "Rejected files" else "Album sync") },
                navigationIcon = {
                    if (state.rejectedVisible) {
                        IconButton(onClick = actions.onHideRejected) {
                            Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
                        }
                    } else {
                        IconButton(onClick = actions.onMenu) {
                            Icon(Icons.Default.Menu, contentDescription = "Open menu")
                        }
                    }
                },
            )
        },
    ) { padding ->
        Box(Modifier.fillMaxSize().padding(padding)) {
            when {
                !state.loaded -> Column(Modifier.align(Alignment.Center), horizontalAlignment = Alignment.CenterHorizontally) {
                    CircularProgressIndicator()
                    Spacer(Modifier.size(12.dp))
                    Text("Loading...", style = MaterialTheme.typography.bodyMedium)
                }
                state.environment == null -> Text(
                    "No server environment is selected. Choose one in the app menu, then open Album sync again.",
                    Modifier.padding(16.dp).testTag("sync_no_environment"),
                    style = MaterialTheme.typography.bodyLarge,
                    color = MaterialTheme.colorScheme.error,
                )
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

    state.chooser?.let { GalleryChooserDialog(it, actions) }

    (state.removal as? RemovalState.Confirm)?.let { confirm ->
        RemoveSyncedDialog(
            confirm = confirm,
            environmentName = state.environment?.name.orEmpty(),
            albumName = state.album?.displayName.orEmpty(),
            onConfirm = actions.onConfirmRemoval,
            onDismiss = actions.onCancelRemoval,
        )
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

/** Screen-reader announcement of a changed text. */
private fun Modifier.announce(mode: Announce): Modifier = when (mode) {
    Announce.NONE -> this
    Announce.POLITE -> semantics { liveRegion = LiveRegionMode.Polite }
    Announce.ASSERTIVE -> semantics { liveRegion = LiveRegionMode.Assertive }
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
                onClick = actions.onChooseGallery,
                enabled = state.controls.canChooseAlbum,
                modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp),
            ) {
                Icon(Icons.Default.Folder, contentDescription = null, modifier = Modifier.size(20.dp))
                Spacer(Modifier.width(8.dp))
                Text("Gallery album...")
            }
            OutlinedButton(
                onClick = actions.onChooseAlbum,
                enabled = state.controls.canChooseAlbum,
                modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp),
            ) { Text("Folder...") }
        }
    }
}

/** A gallery album is re-picked through the gallery chooser (it also asks for photo access), a folder through the picker. */
private fun repickAction(state: SyncUiState, actions: SyncActions): () -> Unit =
    if (repickUsesGallery(state.album)) actions.onChooseGallery else actions.onChooseAlbum

@Composable
private fun AlbumSections(state: SyncUiState, actions: SyncActions) {
    val controls = state.controls
    StatusCard(state, actions)
    // The one place that reports a lost grant, with the one "Choose album again" (see lostAccessCardVisible).
    if (lostAccessCardVisible(state.scan)) {
        Card(Modifier.fillMaxWidth()) {
            Column(Modifier.padding(16.dp).announce(Announce.POLITE), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                ProblemText(ACCESS_LOST_TITLE)
                Text(accessLostHelp(state.album), style = MaterialTheme.typography.bodyMedium)
                Button(onClick = repickAction(state, actions), enabled = !state.busy, modifier = Modifier.heightIn(min = 48.dp)) {
                    Text("Choose album again")
                }
            }
        }
    }
    SyncCard(state, actions)
    BatchSizeRow(state, actions, controls)
    HorizontalDivider()
    Text("Change album", style = MaterialTheme.typography.titleSmall)
    OutlinedButton(
        onClick = actions.onChooseGallery,
        enabled = controls.canChangeAlbum,
        modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp),
    ) {
        Icon(Icons.Default.Folder, contentDescription = null, modifier = Modifier.size(20.dp))
        Spacer(Modifier.width(8.dp))
        Text("Gallery album...")
    }
    OutlinedButton(
        onClick = actions.onChooseAlbum,
        enabled = controls.canChangeAlbum,
        modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp),
    ) { Text("Folder...") }
    if (state.removalSupported && repickUsesGallery(state.album)) {
        HorizontalDivider()
        Text("Free up space", style = MaterialTheme.typography.titleSmall)
        OutlinedButton(
            onClick = actions.onRemoveSynced,
            enabled = state.canRemoveSynced,
            modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp),
        ) {
            Icon(Icons.Default.Delete, contentDescription = null, modifier = Modifier.size(20.dp))
            Spacer(Modifier.width(8.dp))
            Text(if (state.removal == RemovalState.Checking) "Checking..." else "Move synced photos to the trash...")
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
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
            // The headline above already says it; the bar is decoration for a screen reader (no double announcement).
            LinearProgressIndicator(
                progress = { progressFraction(counts) },
                modifier = Modifier.fillMaxWidth().clearAndSetSemantics { },
            )
            FlowRow(
                horizontalArrangement = Arrangement.spacedBy(16.dp),
                verticalArrangement = Arrangement.spacedBy(4.dp),
                itemVerticalAlignment = Alignment.CenterVertically,
            ) {
                Text("${counts.pending} pending", style = MaterialTheme.typography.bodyMedium, modifier = Modifier.testTag("sync_pending"))
                OutlinedButton(onClick = actions.onShowRejected, modifier = Modifier.heightIn(min = 48.dp)) {
                    Text("${counts.rejected} rejected", modifier = Modifier.testTag("sync_rejected"))
                }
            }
            scanStatusText(state.scan)?.let { text ->
                Row(Modifier.announce(Announce.POLITE), verticalAlignment = Alignment.CenterVertically) {
                    if (scanIsProblem(state.scan)) {
                        ProblemText(text)
                    } else {
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
                Spacer(Modifier.width(8.dp))
                Text("Refresh")
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
                    Spacer(Modifier.width(8.dp))
                    Text("Stop")
                }
                Text("Keep the app open while syncing", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            } else {
                Button(
                    onClick = actions.onSyncNow,
                    enabled = controls.canSync,
                    modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp),
                ) {
                    Icon(Icons.Default.Sync, contentDescription = null, modifier = Modifier.size(20.dp))
                    Spacer(Modifier.width(8.dp))
                    Text("Sync now")
                }
                nothingToSendText(state.counts, state.scan, sync)?.let {
                    Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            if (!syncStatusHidden(state.scan, sync)) {
                syncStatusText(sync)?.let { text ->
                    Row(verticalAlignment = Alignment.Top) {
                        Column(Modifier.weight(1f).testTag("sync_status").announce(syncAnnouncement(sync))) {
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
            }
            if (repickInSyncCard(state.scan, sync)) {
                Button(onClick = repickAction(state, actions), modifier = Modifier.heightIn(min = 48.dp)) { Text("Choose album again") }
            }
        }
    }
}

@Composable
private fun BatchSizeRow(state: SyncUiState, actions: SyncActions, controls: SyncControls) {
    var draft by remember(state.batchSize) { mutableFloatStateOf(state.batchSize.toFloat()) }
    val perRequest = "${fileCount(draft.roundToInt())} per request"
    Column(Modifier.fillMaxWidth()) {
        Text("Batch size: $perRequest", style = MaterialTheme.typography.bodyMedium)
        // No tick marks (49 ticks are noise on a phone). The thumb follows the finger exactly (rounding the drawn value
        // made it lag behind and jump off the track); the label shows whole files and the value snaps and is saved
        // when the drag ends.
        Slider(
            value = draft,
            onValueChange = { draft = it },
            onValueChangeFinished = {
                draft = draft.roundToInt().toFloat()
                actions.onBatchSizeChange(draft.toInt())
            },
            valueRange = TrackerSettingsStore.MIN_BATCH_SIZE.toFloat()..TrackerSettingsStore.MAX_BATCH_SIZE.toFloat(),
            modifier = Modifier.semantics { stateDescription = perRequest },
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
    // Explanation, "Retry all" and the empty text are list items, so large text or landscape never squeezes the list.
    LazyColumn(Modifier.fillMaxSize()) {
        item(key = "header") {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(
                    "Rejected files stay skipped. Only files the server failed on can be retried.",
                    style = MaterialTheme.typography.bodyMedium,
                )
                if (state.retryableCount > 0) {
                    Button(onClick = actions.onRetryAll, modifier = Modifier.heightIn(min = 48.dp)) {
                        Icon(Icons.Default.Replay, contentDescription = null, modifier = Modifier.size(20.dp))
                        Spacer(Modifier.width(8.dp))
                        Text("Retry all server errors (${state.retryableCount})")
                    }
                }
            }
        }
        if (state.rejectedLoading) {
            item(key = "loading") { Text("Loading...", Modifier.padding(16.dp), style = MaterialTheme.typography.bodyMedium) }
        } else if (state.rejected.isEmpty()) {
            item(key = "empty") { Text("No rejected files.", Modifier.padding(16.dp), style = MaterialTheme.typography.bodyMedium) }
        }
        items(state.rejected, key = { it.albumTreeUri + "|" + it.documentId }) { item ->
            Row(
                Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                // Name and reason are one focus stop; the ellipsized name stays complete for a screen reader.
                Column(Modifier.weight(1f).semantics(mergeDescendants = true) { }) {
                    Text(item.displayName, style = MaterialTheme.typography.bodyMedium, maxLines = 2, overflow = TextOverflow.Ellipsis)
                    Text(item.reasonText, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                if (item.canRetry) {
                    TextButton(
                        onClick = { actions.onRetryOne(item) },
                        modifier = Modifier.heightIn(min = 48.dp).semantics { contentDescription = "Retry ${item.displayName}" },
                    ) { Text("Retry") }
                }
            }
            HorizontalDivider()
        }
    }
}

@Composable
private fun RemoveSyncedDialog(
    confirm: RemovalState.Confirm,
    environmentName: String,
    albumName: String,
    onConfirm: () -> Unit,
    onDismiss: () -> Unit,
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(removeSyncedTitle(confirm.count)) },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                removeSyncedLines(confirm.count, confirm.kept, environmentName, albumName).forEach {
                    Text(it, style = MaterialTheme.typography.bodyMedium)
                }
            }
        },
        confirmButton = {
            TextButton(
                onClick = onConfirm,
                colors = androidx.compose.material3.ButtonDefaults.textButtonColors(contentColor = MaterialTheme.colorScheme.error),
                modifier = Modifier.heightIn(min = 48.dp),
            ) { Text("Move ${confirm.count}") }
        },
        dismissButton = {
            TextButton(onClick = onDismiss, modifier = Modifier.heightIn(min = 48.dp)) { Text("Cancel") }
        },
    )
}

@Composable
private fun ChangeAlbumDialog(lines: List<DialogLine>, busy: Boolean, onConfirm: () -> Unit, onDismiss: () -> Unit) {
    AlertDialog(
        onDismissRequest = { if (!busy) onDismiss() },
        title = { Text("Change album?") },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                lines.forEach { line ->
                    if (line.isWarning) ProblemText(line.text) else Text(line.text, style = MaterialTheme.typography.bodyMedium)
                }
            }
        },
        confirmButton = {
            // The destructive action is visibly distinct: error colour. Cancel stays a normal text button.
            TextButton(
                onClick = onConfirm,
                enabled = !busy,
                colors = androidx.compose.material3.ButtonDefaults.textButtonColors(contentColor = MaterialTheme.colorScheme.error),
                modifier = Modifier.heightIn(min = 48.dp),
            ) { Text("Change and reset") }
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
