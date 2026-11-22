package com.memebrowser.app.data.sync

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.util.Log
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.update
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicLong
import javax.inject.Inject
import javax.inject.Singleton

/**
 * [NetworkGate] over the default network (A6). One default-network callback, registered lazily on first use with the
 * application context and never unregistered (the gate is a process singleton), bumps [version] on every
 * onAvailable / onLost / onCapabilitiesChanged.
 *
 * Race tolerance: [status] remembers the version it was computed at, and [awaitChange] returns as soon as the version
 * is past it, so an event that arrives between the two calls is not missed. The remembered version is shared by all
 * callers (two environments waiting at once can still miss one event); the engine's 5 s recheck is the backstop.
 */
@Singleton
class AndroidNetworkGate @Inject constructor(@ApplicationContext context: Context) : NetworkGate {
    private val connectivity = context.applicationContext.getSystemService(ConnectivityManager::class.java)
    private val version = MutableStateFlow(0L)
    private val observed = AtomicLong(0L)
    private val registered = AtomicBoolean(false)

    private val callback = object : ConnectivityManager.NetworkCallback() {
        override fun onAvailable(network: Network) = bump()
        override fun onLost(network: Network) = bump()
        override fun onCapabilitiesChanged(network: Network, networkCapabilities: NetworkCapabilities) = bump()
    }

    private fun bump() {
        version.update { it + 1 }
    }

    private fun ensureRegistered() {
        if (!registered.compareAndSet(false, true)) return
        try {
            connectivity.registerDefaultNetworkCallback(callback)
        } catch (e: RuntimeException) { // SecurityException, or TooManyRequestsException on some vendors
            registered.set(false)
            Log.w(TAG, "Could not register the network callback", e)
        }
    }

    override fun status(wifiOnly: Boolean): NetworkStatus {
        ensureRegistered()
        observed.set(version.value) // before reading the capabilities: a later event is then seen by awaitChange
        val caps = try {
            connectivity.activeNetwork?.let { connectivity.getNetworkCapabilities(it) }
        } catch (e: SecurityException) {
            Log.w(TAG, "Could not read the network state", e)
            return NetworkStatus.OK // never block a sync on a broken platform call; the upload itself reports NETWORK
        }
        if (caps == null || !caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED)) return NetworkStatus.NONE
        if (wifiOnly && !caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_METERED)) return NetworkStatus.NEEDS_WIFI
        return NetworkStatus.OK
    }

    override suspend fun awaitChange() {
        ensureRegistered()
        val since = observed.get()
        version.first { it > since }
    }

    private companion object {
        const val TAG = "AndroidNetworkGate"
    }
}
