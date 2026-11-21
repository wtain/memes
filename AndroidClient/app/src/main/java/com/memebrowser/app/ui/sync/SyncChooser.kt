package com.memebrowser.app.ui.sync

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.memebrowser.app.data.album.GalleryAlbum
import com.memebrowser.app.data.album.MediaAccess

/** The gallery album chooser: a list of albums, or a text that says why there is none (never a blank dialog). */
@Composable
internal fun GalleryChooserDialog(chooser: ChooserState, actions: SyncActions) {
    AlertDialog(
        onDismissRequest = actions.onChooserDismiss,
        title = { Text("Choose a gallery album") },
        text = {
            when (chooser) {
                ChooserState.Loading -> Text("Loading albums...", style = MaterialTheme.typography.bodyMedium)
                ChooserState.Empty -> Text("No albums with images were found on this phone.", style = MaterialTheme.typography.bodyMedium)
                ChooserState.Failed -> Text("The gallery could not be read. Close this and try again.", style = MaterialTheme.typography.bodyMedium)
                is ChooserState.NeedsPermission -> PermissionCard(chooser.access, actions)
                is ChooserState.Albums -> AlbumList(chooser.albums, actions.onChooseGalleryAlbum)
            }
        },
        confirmButton = {},
        dismissButton = {
            TextButton(onClick = actions.onChooserDismiss, modifier = Modifier.heightIn(min = 48.dp)) { Text("Cancel") }
        },
    )
}

@Composable
private fun AlbumList(albums: List<GalleryAlbum>, onChoose: (GalleryAlbum) -> Unit) {
    LazyColumn(Modifier.fillMaxWidth().testTag("gallery_album_list")) {
        items(albums, key = { it.key }) { album ->
            Column(
                Modifier
                    .fillMaxWidth()
                    .heightIn(min = 48.dp)
                    .clickable(role = Role.Button) { onChoose(album) }
                    .padding(vertical = 8.dp),
                verticalArrangement = Arrangement.Center,
            ) {
                Text(album.name, style = MaterialTheme.typography.bodyLarge, maxLines = 2, overflow = TextOverflow.Ellipsis)
                Text(
                    "${album.imageCount} images",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                album.pathHint?.let {
                    Text(
                        it,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
            }
            HorizontalDivider()
        }
    }
}

/** What is missing for the gallery and the one action that helps: ask again (DENIED) or open the system settings. */
@Composable
internal fun PermissionCard(access: MediaAccess, actions: SyncActions) {
    Column(Modifier.testTag("gallery_permission_card"), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text(permissionCardText(access), style = MaterialTheme.typography.bodyMedium)
        when (access) {
            MediaAccess.PARTIAL, MediaAccess.BLOCKED ->
                Button(onClick = actions.onOpenAppSettings, modifier = Modifier.heightIn(min = 48.dp)) { Text("Open settings") }
            MediaAccess.DENIED ->
                Button(onClick = actions.onRequestGalleryPermission, modifier = Modifier.heightIn(min = 48.dp)) { Text("Allow access") }
            MediaAccess.FULL -> Unit
        }
    }
}
