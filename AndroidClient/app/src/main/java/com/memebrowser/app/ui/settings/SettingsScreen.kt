package com.memebrowser.app.ui.settings

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Slider
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.memebrowser.app.data.settings.AppSettingsStore
import com.memebrowser.app.data.tracker.TrackerSettingsStore

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(
    onBack: () -> Unit,
    viewModel: SettingsViewModel = hiltViewModel()
) {
    val state by viewModel.state.collectAsStateWithLifecycle()

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Settings") },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
                    }
                }
            )
        }
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 16.dp, vertical = 8.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            SectionTitle("Updates")
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                Column(modifier = Modifier.weight(1f)) {
                    Text("Check for updates on startup", style = MaterialTheme.typography.bodyLarge)
                    Text(
                        "Ask to install a newer version when the app starts.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
                Switch(
                    checked = state.checkUpdatesOnStartup,
                    onCheckedChange = viewModel::setCheckUpdatesOnStartup,
                    modifier = Modifier.testTag("check_updates_switch")
                )
            }

            HorizontalDivider(modifier = Modifier.padding(vertical = 8.dp))
            SectionTitle("Search")
            SliderSetting(
                label = "Results per page",
                valueText = "${state.resultsPerPage}",
                value = state.resultsPerPage,
                range = AppSettingsStore.MIN_RESULTS_PER_PAGE..AppSettingsStore.MAX_RESULTS_PER_PAGE,
                step = 10,
                onChangeFinished = viewModel::setResultsPerPage,
            )
            SliderSetting(
                label = "Minimum thumbnail size",
                valueText = "${state.gridMinSizeDp} dp",
                value = state.gridMinSizeDp,
                range = AppSettingsStore.MIN_GRID_MIN_SIZE_DP..AppSettingsStore.MAX_GRID_MIN_SIZE_DP,
                step = 10,
                hint = "Smaller means more columns.",
                onChangeFinished = viewModel::setGridMinSizeDp,
            )

            HorizontalDivider(modifier = Modifier.padding(vertical = 8.dp))
            SectionTitle("Album sync")
            SliderSetting(
                label = "Upload batch size",
                valueText = "${state.uploadBatchSize} per request",
                value = state.uploadBatchSize,
                range = TrackerSettingsStore.MIN_BATCH_SIZE..TrackerSettingsStore.MAX_BATCH_SIZE,
                step = 1,
                hint = "A change applies from the next batch.",
                onChangeFinished = viewModel::setUploadBatchSize,
            )
        }
    }
}

@Composable
private fun SectionTitle(text: String) {
    Text(text, style = MaterialTheme.typography.titleMedium, color = MaterialTheme.colorScheme.primary)
}

@Composable
private fun SliderSetting(
    label: String,
    valueText: String,
    value: Int,
    range: IntRange,
    step: Int,
    onChangeFinished: (Int) -> Unit,
    hint: String? = null,
) {
    var draft by remember(value) { mutableFloatStateOf(value.toFloat()) }
    val steps = ((range.last - range.first) / step - 1).coerceAtLeast(0)
    Column {
        Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            Text(label, style = MaterialTheme.typography.bodyLarge)
            Text(
                if (draft.toInt() == value) valueText else draft.toInt().toString(),
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )
        }
        Slider(
            value = draft,
            onValueChange = { draft = it },
            onValueChangeFinished = { onChangeFinished(draft.toInt()) },
            valueRange = range.first.toFloat()..range.last.toFloat(),
            steps = steps,
        )
        if (hint != null) {
            Text(hint, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}
