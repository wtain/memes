package com.memebrowser.app.data.sync

import kotlinx.coroutines.awaitCancellation

/** What the phone's network allows a sync to do right now. */
enum class NetworkStatus {
    /** A usable network (unmetered, or any validated one when Wi-Fi only is off). */
    OK,

    /** Wi-Fi only is on and the only validated network is metered. */
    NEEDS_WIFI,

    /** No validated network at all. */
    NONE,
}

/**
 * The engine's view of the network (A6). [status] is evaluated for the CURRENT Wi-Fi-only setting, which the engine
 * re-reads before every evaluation, so a setting change applies to a waiting run.
 */
interface NetworkGate {
    fun status(wifiOnly: Boolean): NetworkStatus

    /** Returns when the network situation changes (any change). Cancellable. */
    suspend fun awaitChange()
}

/** No network checks: always OK, never changes. The default where no real gate is bound (tests, previews). */
object AlwaysOnline : NetworkGate {
    override fun status(wifiOnly: Boolean): NetworkStatus = NetworkStatus.OK
    override suspend fun awaitChange() = awaitCancellation()
}
