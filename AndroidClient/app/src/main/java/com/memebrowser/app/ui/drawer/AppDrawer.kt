package com.memebrowser.app.ui.drawer

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ShowChart
import androidx.compose.material.icons.filled.BarChart
import androidx.compose.material.icons.filled.Block
import androidx.compose.material.icons.filled.Dns
import androidx.compose.material.icons.filled.Info
import androidx.compose.material.icons.filled.Recommend
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Upload
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationDrawerItem
import androidx.compose.material3.NavigationDrawerItemDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import com.memebrowser.app.data.capabilities.FeatureVisibility
import com.memebrowser.app.ui.search.HealthStatus

/**
 * The app menu shared by the Search and Album sync screens. A screen that does not check the server passes a null
 * [healthStatus] and the status row is left out. [albumSyncSelected] marks the Album sync item as the current screen.
 */
@Composable
fun DrawerContent(
    healthStatus: HealthStatus?,
    visibility: FeatureVisibility,
    onUploadClick: () -> Unit,
    onFlaggedClick: () -> Unit,
    onEnvironmentsClick: () -> Unit,
    onSettingsClick: () -> Unit,
    onAboutClick: () -> Unit,
    onTrendsClick: () -> Unit,
    onRecommendationsClick: () -> Unit,
    onStatisticsClick: () -> Unit,
    albumSyncSelected: Boolean = false,
) {
    Spacer(Modifier.height(16.dp))
    Text(
        text = "MemeBrowser",
        style = MaterialTheme.typography.titleLarge,
        modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp)
    )

    if (healthStatus != null) HealthRow(healthStatus)

    HorizontalDivider(modifier = Modifier.padding(vertical = 8.dp))

    if (visibility.showAlbumSync) {
        NavigationDrawerItem(
            icon = { Icon(Icons.Default.Upload, contentDescription = null) },
            label = { Text("Album sync") },
            selected = albumSyncSelected,
            onClick = onUploadClick,
            modifier = Modifier.padding(NavigationDrawerItemDefaults.ItemPadding)
        )
    }
    if (visibility.showFlagged) {
        NavigationDrawerItem(
            icon = { Icon(Icons.Default.Block, contentDescription = null) },
            label = { Text("Flagged") },
            selected = false,
            onClick = onFlaggedClick,
            modifier = Modifier.padding(NavigationDrawerItemDefaults.ItemPadding)
        )
    }
    NavigationDrawerItem(
        icon = { Icon(Icons.Default.Dns, contentDescription = null) },
        label = { Text("Environments") },
        selected = false,
        onClick = onEnvironmentsClick,
        modifier = Modifier.padding(NavigationDrawerItemDefaults.ItemPadding)
    )
    NavigationDrawerItem(
        icon = { Icon(Icons.Default.Settings, contentDescription = null) },
        label = { Text("Settings") },
        selected = false,
        onClick = onSettingsClick,
        modifier = Modifier.padding(NavigationDrawerItemDefaults.ItemPadding)
    )
    NavigationDrawerItem(
        icon = { Icon(Icons.Default.Info, contentDescription = null) },
        label = { Text("About") },
        selected = false,
        onClick = onAboutClick,
        modifier = Modifier.padding(NavigationDrawerItemDefaults.ItemPadding)
    )

    HorizontalDivider(modifier = Modifier.padding(vertical = 8.dp))

    if (visibility.showTrends) {
        NavigationDrawerItem(
            icon = { Icon(Icons.AutoMirrored.Filled.ShowChart, contentDescription = null) },
            label = { Text("Trends") },
            selected = false,
            onClick = onTrendsClick,
            modifier = Modifier.padding(NavigationDrawerItemDefaults.ItemPadding)
        )
    }
    NavigationDrawerItem(
        icon = { Icon(Icons.Default.Recommend, contentDescription = null) },
        label = { Text("Recommendations") },
        selected = false,
        onClick = onRecommendationsClick,
        modifier = Modifier.padding(NavigationDrawerItemDefaults.ItemPadding)
    )
    if (visibility.showStatistics) {
        NavigationDrawerItem(
            icon = { Icon(Icons.Default.BarChart, contentDescription = null) },
            label = { Text("Statistics") },
            selected = false,
            onClick = onStatisticsClick,
            modifier = Modifier.padding(NavigationDrawerItemDefaults.ItemPadding)
        )
    }
}

@Composable
private fun HealthRow(healthStatus: HealthStatus) {
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
        Text(
            text = healthLabel, style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )
    }
}
