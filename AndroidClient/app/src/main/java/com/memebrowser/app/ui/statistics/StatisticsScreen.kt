package com.memebrowser.app.ui.statistics

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.ui.draw.clip
import androidx.compose.ui.layout.ContentScale
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import coil.compose.AsyncImage
import com.memebrowser.app.data.model.StatisticsResponse

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun StatisticsScreen(
    onBack: () -> Unit,
    viewModel: StatisticsViewModel = hiltViewModel()
) {
    val state by viewModel.state.collectAsStateWithLifecycle()

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Statistics") },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
                    }
                }
            )
        }
    ) { padding ->
        Box(Modifier.fillMaxSize().padding(padding)) {
            when (val s = state) {
                is StatisticsViewModel.UiState.Loading ->
                    CircularProgressIndicator(Modifier.align(Alignment.Center))

                is StatisticsViewModel.UiState.Error ->
                    Column(
                        Modifier.align(Alignment.Center),
                        horizontalAlignment = Alignment.CenterHorizontally
                    ) {
                        Text(s.message, color = MaterialTheme.colorScheme.error)
                        Spacer(Modifier.height(8.dp))
                        Button(onClick = { viewModel.load() }) { Text("Retry") }
                    }

                is StatisticsViewModel.UiState.Success ->
                    StatisticsContent(s.data)
            }
        }
    }
}

@Composable
private fun StatisticsContent(data: StatisticsResponse) {
    val total = data.memes.total.toFloat().coerceAtLeast(1f)

    fun pct(n: Int) = "$n (${"%.1f".format(n * 100f / total)}%)"

    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp)
    ) {
        AsyncImage(
            model = "https://imgflip.com/s/meme/Philosoraptor.jpg",
            contentDescription = "Philosoraptor",
            contentScale = ContentScale.Crop,
            modifier = Modifier.size(64.dp).clip(CircleShape).align(Alignment.CenterHorizontally)
        )
        StatSection("Library") {
            StatRow("Total memes", "${data.memes.total}")
            StatRow("Excluded", "${data.memes.excluded}")
        }
        StatSection("Pipeline coverage") {
            StatRow("With OCR", pct(data.memes.withOcr))
            StatRow("With embeddings", pct(data.memes.withEmbeddings))
            StatRow("With tags", pct(data.memes.withTags))
            StatRow("Untagged", pct(data.memes.total - data.memes.withTags))
            StatRow("With descriptions", pct(data.memes.withDescriptions))
            StatRow("With concept assignments", pct(data.memes.withConceptTags))
        }
        StatSection("Tags") {
            StatRow("Total tags", "${data.content.tags}")
            val avg = if (data.memes.withTags > 0)
                "%.1f".format(data.content.tags.toFloat() / data.memes.withTags)
            else "—"
            StatRow("Avg tags / tagged meme", avg)
            StatRow("Tag categories", "${data.content.tagKeys}")
            StatRow("Distinct tag values", "${data.content.tagValues}")
            StatRow("OCR text blocks", "${data.content.ocrTexts}")
        }
        StatSection("Knowledge base") {
            StatRow("Concepts", "${data.content.concepts}")
            StatRow("Concept image sets", "${data.content.conceptImageSets}")
        }
    }
}

@Composable
private fun StatSection(title: String, content: @Composable ColumnScope.() -> Unit) {
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp)) {
            Text(title, style = MaterialTheme.typography.titleMedium)
            Spacer(Modifier.height(8.dp))
            content()
        }
    }
}

@Composable
private fun StatRow(label: String, value: String) {
    Row(
        Modifier
            .fillMaxWidth()
            .padding(vertical = 4.dp),
        horizontalArrangement = Arrangement.SpaceBetween
    ) {
        Text(
            label,
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )
        Text(value, style = MaterialTheme.typography.bodyMedium)
    }
}