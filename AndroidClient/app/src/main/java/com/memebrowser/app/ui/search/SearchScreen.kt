package com.memebrowser.app.ui.search

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.GridItemSpan
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.lazy.grid.rememberLazyGridState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Block
import androidx.compose.material.icons.filled.Clear
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Menu
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Tune
import androidx.compose.material.icons.filled.Upload
import androidx.compose.material3.Badge
import androidx.compose.material3.BadgedBox
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DrawerValue
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.InputChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.ModalDrawerSheet
import androidx.compose.material3.ModalNavigationDrawer
import androidx.compose.material3.NavigationDrawerItem
import androidx.compose.material3.NavigationDrawerItemDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TextField
import androidx.compose.material3.TextFieldDefaults
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.rememberDrawerState
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import coil.compose.AsyncImage
import com.memebrowser.app.data.model.Facet
import com.memebrowser.app.data.model.Meme
import kotlinx.coroutines.launch

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SearchScreen(
    onMemeClick: (String) -> Unit,
    onEnvironmentsClick: () -> Unit,
    onExcludedClick: () -> Unit,
    onUploadClick: () -> Unit = {},
    viewModel: SearchViewModel = hiltViewModel()
) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    val snackbarHostState = remember { SnackbarHostState() }
    var showFacetSheet by remember { mutableStateOf(false) }
    val drawerState = rememberDrawerState(initialValue = DrawerValue.Closed)
    val scope = rememberCoroutineScope()

    LaunchedEffect(state.error) {
        if (state.error != null) {
            snackbarHostState.showSnackbar(state.error!!)
            viewModel.dismissError()
        }
    }

    ModalNavigationDrawer(
        drawerState = drawerState,
        drawerContent = {
            ModalDrawerSheet {
                DrawerContent(
                    healthStatus = state.healthStatus,
                    onUploadClick = {
                        scope.launch { drawerState.close() }
                        onUploadClick()
                    },
                    onExcludedClick = {
                        scope.launch { drawerState.close() }
                        onExcludedClick()
                    },
                    onEnvironmentsClick = {
                        scope.launch { drawerState.close() }
                        onEnvironmentsClick()
                        viewModel.checkHealth()
                    }
                )
            }
        }
    ) {
        Scaffold(
            snackbarHost = { SnackbarHost(snackbarHostState) },
            topBar = {
                Column {
                    TopAppBar(
                        navigationIcon = {
                            IconButton(onClick = { scope.launch { drawerState.open() } }) {
                                Icon(Icons.Default.Menu, contentDescription = "Open menu")
                            }
                        },
                        title = {
                            TextField(
                                value = state.query,
                                onValueChange = viewModel::onQueryChange,
                                placeholder = { Text("Search memes…") },
                                singleLine = true,
                                trailingIcon = {
                                    if (state.query.isNotEmpty()) {
                                        IconButton(onClick = { viewModel.onQueryChange("") }) {
                                            Icon(Icons.Default.Clear, contentDescription = "Clear")
                                        }
                                    }
                                },
                                colors = TextFieldDefaults.colors(
                                    focusedContainerColor = Color.Transparent,
                                    unfocusedContainerColor = Color.Transparent,
                                    focusedIndicatorColor = Color.Transparent,
                                    unfocusedIndicatorColor = Color.Transparent
                                ),
                                modifier = Modifier.fillMaxWidth()
                            )
                        },
                        actions = {
                            BadgedBox(
                                badge = {
                                    if (state.activeFacets.isNotEmpty()) {
                                        Badge { Text("${state.activeFacets.size}") }
                                    }
                                }
                            ) {
                                IconButton(onClick = { showFacetSheet = true }) {
                                    Icon(Icons.Default.Tune, contentDescription = "Filters")
                                }
                            }
                        }
                    )
                    if (state.activeFacets.isNotEmpty()) {
                        ActiveFacetsRow(
                            facets = state.activeFacets,
                            onRemove = viewModel::removeFacet
                        )
                    }
                }
            }
        ) { paddingValues ->
            Box(modifier = Modifier.padding(paddingValues).fillMaxSize()) {
                if (state.isLoading) {
                    CircularProgressIndicator(modifier = Modifier.align(Alignment.Center))
                } else {
                    MemeGrid(
                        items = state.items,
                        hasNext = state.hasNext,
                        isLoadingMore = state.isLoadingMore,
                        onMemeClick = onMemeClick,
                        onLoadMore = viewModel::loadMore
                    )
                }
            }
        }
    }

    if (showFacetSheet) {
        FacetBottomSheet(
            facets = state.facets,
            activeFacets = state.activeFacets,
            onToggle = viewModel::onFacetToggle,
            onDismiss = { showFacetSheet = false }
        )
    }
}

@Composable
private fun DrawerContent(
    healthStatus: HealthStatus,
    onUploadClick: () -> Unit,
    onExcludedClick: () -> Unit,
    onEnvironmentsClick: () -> Unit
) {
    Spacer(Modifier.height(16.dp))
    Text(
        text = "MemeBrowser",
        style = MaterialTheme.typography.titleLarge,
        modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp)
    )

    val healthColor = when (healthStatus) {
        HealthStatus.Online -> Color(0xFF4CAF50)
        HealthStatus.Offline -> MaterialTheme.colorScheme.error
        HealthStatus.Unknown -> Color(0xFFFF9800)
    }
    val healthLabel = when (healthStatus) {
        HealthStatus.Online -> "Server online"
        HealthStatus.Offline -> "Server offline"
        HealthStatus.Unknown -> "Connecting…"
    }
    Row(
        modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Box(
            modifier = Modifier
                .size(8.dp)
                .background(color = healthColor, shape = RoundedCornerShape(50))
        )
        Spacer(Modifier.width(8.dp))
        Text(text = healthLabel, style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
    }

    HorizontalDivider(modifier = Modifier.padding(vertical = 8.dp))

    NavigationDrawerItem(
        icon = { Icon(Icons.Default.Upload, contentDescription = null) },
        label = { Text("Upload") },
        selected = false,
        onClick = onUploadClick,
        modifier = Modifier.padding(NavigationDrawerItemDefaults.ItemPadding)
    )
    NavigationDrawerItem(
        icon = { Icon(Icons.Default.Block, contentDescription = null) },
        label = { Text("Excluded") },
        selected = false,
        onClick = onExcludedClick,
        modifier = Modifier.padding(NavigationDrawerItemDefaults.ItemPadding)
    )
    NavigationDrawerItem(
        icon = { Icon(Icons.Default.Settings, contentDescription = null) },
        label = { Text("Environments") },
        selected = false,
        onClick = onEnvironmentsClick,
        modifier = Modifier.padding(NavigationDrawerItemDefaults.ItemPadding)
    )
}

@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
private fun FacetBottomSheet(
    facets: List<Facet>,
    activeFacets: List<ActiveFacet>,
    onToggle: (String, String) -> Unit,
    onDismiss: () -> Unit
) {
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = sheetState
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 16.dp)
                .padding(bottom = 32.dp)
        ) {
            Text(
                text = "Filters",
                style = MaterialTheme.typography.titleMedium,
                modifier = Modifier.padding(bottom = 16.dp)
            )
            if (facets.isEmpty()) {
                Text(
                    text = "No filters available yet — run a search first",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            } else {
                facets.forEach { facet ->
                    Text(
                        text = facet.name,
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(bottom = 8.dp)
                    )
                    FlowRow(
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(bottom = 16.dp)
                    ) {
                        facet.buckets.forEach { bucket ->
                            val isActive = activeFacets.any {
                                it.category == facet.name && it.value == bucket.value
                            }
                            FilterChip(
                                selected = isActive,
                                onClick = { onToggle(facet.name, bucket.value) },
                                label = { Text("${bucket.value} (${bucket.count.toInt()})") }
                            )
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun ActiveFacetsRow(
    facets: List<ActiveFacet>,
    onRemove: (ActiveFacet) -> Unit
) {
    Row(
        modifier = Modifier
            .horizontalScroll(rememberScrollState())
            .padding(horizontal = 12.dp, vertical = 4.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp)
    ) {
        facets.forEach { facet ->
            InputChip(
                selected = true,
                onClick = { onRemove(facet) },
                label = { Text("${facet.category}:${facet.value}") },
                trailingIcon = {
                    Icon(
                        Icons.Default.Close,
                        contentDescription = null,
                        modifier = Modifier.size(16.dp)
                    )
                }
            )
        }
    }
}

@Composable
private fun MemeGrid(
    items: List<Meme>,
    hasNext: Boolean,
    isLoadingMore: Boolean,
    onMemeClick: (String) -> Unit,
    onLoadMore: () -> Unit
) {
    val gridState = rememberLazyGridState()

    LaunchedEffect(gridState) {
        snapshotFlow { gridState.layoutInfo }
            .collect { info ->
                val lastVisible = info.visibleItemsInfo.lastOrNull()?.index ?: return@collect
                if (lastVisible >= info.totalItemsCount - 4 && hasNext && !isLoadingMore) {
                    onLoadMore()
                }
            }
    }

    LazyVerticalGrid(
        columns = GridCells.Adaptive(minSize = 160.dp),
        state = gridState,
        contentPadding = PaddingValues(4.dp),
        horizontalArrangement = Arrangement.spacedBy(2.dp),
        verticalArrangement = Arrangement.spacedBy(2.dp),
        modifier = Modifier.fillMaxSize().testTag("meme_grid")
    ) {
        items(items, key = { it.id }) { meme ->
            MemeGridCell(meme = meme, onClick = { onMemeClick(meme.id) }, modifier = Modifier.testTag("meme_cell_${meme.id}"))
        }
        if (isLoadingMore) {
            item(span = { GridItemSpan(maxLineSpan) }) {
                Box(
                    modifier = Modifier.fillMaxWidth().padding(16.dp),
                    contentAlignment = Alignment.Center
                ) {
                    CircularProgressIndicator()
                }
            }
        }
    }
}

@Composable
private fun MemeGridCell(meme: Meme, onClick: () -> Unit, modifier: Modifier = Modifier) {
    Box(
        modifier = modifier
            .aspectRatio(1f)
            .clip(RoundedCornerShape(4.dp))
            .clickable(onClick = onClick)
    ) {
        AsyncImage(
            model = "http://localhost${meme.imageUrl}",
            contentDescription = meme.originalFileName,
            contentScale = ContentScale.Crop,
            modifier = Modifier.fillMaxSize()
        )
        if (meme.excluded == true) {
            Box(
                modifier = Modifier
                    .align(Alignment.TopEnd)
                    .padding(4.dp)
                    .background(
                        color = MaterialTheme.colorScheme.error.copy(alpha = 0.85f),
                        shape = RoundedCornerShape(4.dp)
                    )
                    .padding(horizontal = 4.dp, vertical = 2.dp)
            ) {
                Text(
                    text = "excluded",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onError
                )
            }
        }
    }
}