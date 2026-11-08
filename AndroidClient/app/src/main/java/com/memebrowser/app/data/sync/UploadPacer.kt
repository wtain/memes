package com.memebrowser.app.data.sync

/**
 * Keeps the app under the server's upload rate limit so it almost never sees a 429 (a 429 is answered only after the whole
 * multipart body was transferred, so each one wastes a full batch).
 *
 * The server (Backend/app/services/rate_limit.py, `upload_limiter`) allows [SERVER_MAX_REQUESTS] upload requests per
 * [WINDOW_MS] per client IP. We stay one under it ([MAX_REQUESTS]) as a margin for clock and latency skew. Other clients
 * on the same IP (the web UI, the old Upload screen) share the server's budget, so the 429 handling in the engine stays
 * as the safety net. One pacer per environment id (two environments on one host are not coordinated; the safety net covers it).
 */
internal class UploadPacer(
    private val clock: () -> Long,
    private val maxRequests: Int = MAX_REQUESTS,
    private val windowMs: Long = WINDOW_MS,
) {
    private val sent = ArrayDeque<Long>()

    /** Milliseconds to wait before the next request may start (0 = now). */
    @Synchronized
    fun waitMs(): Long {
        val now = clock()
        while (sent.isNotEmpty() && sent.first() <= now - windowMs) sent.removeFirst()
        return if (sent.size < maxRequests) 0 else sent.first() + windowMs - now
    }

    /** Records that a request starts now. */
    @Synchronized
    fun record() {
        sent.addLast(clock())
    }

    companion object {
        /** The server's `upload_limiter` max_calls. */
        const val SERVER_MAX_REQUESTS = 10
        const val MAX_REQUESTS = SERVER_MAX_REQUESTS - 1
        const val WINDOW_MS = 60_000L
    }
}
