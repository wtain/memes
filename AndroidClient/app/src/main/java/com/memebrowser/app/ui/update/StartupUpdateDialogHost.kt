package com.memebrowser.app.ui.update

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle

/** Shows the startup update dialog on top of whatever screen is current. Put it once, next to the NavHost. */
@Composable
fun StartupUpdateDialogHost(viewModel: StartupUpdateViewModel = hiltViewModel()) {
    val dialog by viewModel.dialog.collectAsStateWithLifecycle()
    when (val d = dialog) {
        StartupUpdateDialog.Hidden -> Unit

        is StartupUpdateDialog.Offer -> AlertDialog(
            onDismissRequest = viewModel::decline,
            title = { Text("Update available") },
            text = { Text("Version ${d.tag} is available. Download and install it now?") },
            confirmButton = { TextButton(onClick = viewModel::install) { Text("Yes") } },
            dismissButton = { TextButton(onClick = viewModel::decline) { Text("No") } },
        )

        is StartupUpdateDialog.Downloading -> AlertDialog(
            onDismissRequest = viewModel::dismiss,
            title = { Text("Downloading update") },
            text = {
                Column {
                    Text("${(d.progress * 100).toInt()}%")
                    LinearProgressIndicator(progress = { d.progress }, modifier = Modifier.fillMaxWidth())
                }
            },
            confirmButton = { TextButton(onClick = viewModel::dismiss) { Text("Hide") } },
        )

        is StartupUpdateDialog.Failed -> AlertDialog(
            onDismissRequest = viewModel::dismiss,
            title = { Text("Update failed") },
            text = { Text(d.message) },
            confirmButton = { TextButton(onClick = viewModel::dismiss) { Text("OK") } },
        )
    }
}
