package com.memebrowser.app.data.sync

/** Asks the platform to keep the process alive while a sync runs (the foreground service, A6). */
interface SyncServiceStarter {
    /** Idempotent: safe to call for every started run. Must not throw. */
    fun ensureRunning()
}

/** Starts nothing: the default for tests and until the real service is bound. */
object NoServiceStarter : SyncServiceStarter {
    override fun ensureRunning() = Unit
}
