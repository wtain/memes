package com.memebrowser.app

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import com.memebrowser.app.service.SyncNotifications
import com.memebrowser.app.ui.NavGraph
import com.memebrowser.app.ui.theme.MemeBrowserTheme
import dagger.hilt.android.AndroidEntryPoint

@AndroidEntryPoint
class MainActivity : ComponentActivity() {
    /** A route asked for by the launching intent (the sync notification), consumed once by [NavGraph]. */
    private var requestedRoute by mutableStateOf<String?>(null)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // Only for a fresh start: a recreated activity (rotation, process restore) already navigated.
        if (savedInstanceState == null) requestedRoute = intent?.getStringExtra(SyncNotifications.EXTRA_ROUTE)
        enableEdgeToEdge()
        setContent {
            MemeBrowserTheme {
                NavGraph(
                    requestedRoute = requestedRoute,
                    onRequestedRouteHandled = { requestedRoute = null },
                )
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        intent.getStringExtra(SyncNotifications.EXTRA_ROUTE)?.let { requestedRoute = it }
    }
}
