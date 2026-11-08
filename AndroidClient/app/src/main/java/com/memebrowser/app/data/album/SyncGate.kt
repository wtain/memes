package com.memebrowser.app.data.album

/**
 * The seam between album changes (A2) and the upload engine (A3). Before an album change wipes an environment, the
 * coordinator calls [cancelAndAwait] so no upload of that environment is still running when the rows go.
 *
 * A3 provides the real implementation (cancel the environment's sync job and join it) and replaces the no-op
 * binding in `di/AlbumModule`.
 */
interface SyncGate {
    /** Cancels any in-flight sync for [environmentId] and returns only once it has stopped. No sync: returns at once. */
    suspend fun cancelAndAwait(environmentId: String)
}

/** Default binding until A3 exists: there is never a sync to stop. */
object NoOpSyncGate : SyncGate {
    override suspend fun cancelAndAwait(environmentId: String) = Unit
}

/** Ids of all known environments (`BackendEnvironment.id`), to decide whether a tree grant is still shared. */
fun interface EnvironmentIdSource {
    suspend fun ids(): List<String>
}
