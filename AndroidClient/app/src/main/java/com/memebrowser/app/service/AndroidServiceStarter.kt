package com.memebrowser.app.service

import android.content.Context
import android.content.Intent
import android.util.Log
import androidx.core.content.ContextCompat
import com.memebrowser.app.data.sync.SyncServiceStarter
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Starts [SyncService] for a run (A6). Never throws: when Android refuses the start (app in the background,
 * `ForegroundServiceStartNotAllowedException`, or a `SecurityException`) the sync still runs in-process, it is only not
 * protected from being frozen.
 */
@Singleton
class AndroidServiceStarter @Inject constructor(@ApplicationContext private val context: Context) : SyncServiceStarter {
    override fun ensureRunning() {
        try {
            ContextCompat.startForegroundService(context, Intent(context, SyncService::class.java))
        } catch (e: IllegalStateException) { // ForegroundServiceStartNotAllowedException is one
            Log.w(TAG, "Could not start the sync service", e)
        } catch (e: SecurityException) {
            Log.w(TAG, "Could not start the sync service", e)
        }
    }

    private companion object {
        const val TAG = "AndroidServiceStarter"
    }
}
