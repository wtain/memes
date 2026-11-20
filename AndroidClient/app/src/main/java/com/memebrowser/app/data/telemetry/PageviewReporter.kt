package com.memebrowser.app.data.telemetry

import com.memebrowser.app.data.model.PageViewBeacon
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch
import retrofit2.Response
import retrofit2.http.Body
import retrofit2.http.POST
import java.util.concurrent.atomic.AtomicBoolean

interface TelemetryApi {
    /** 204 on success; 404 on a backend that does not serve telemetry. */
    @POST("api/telemetry/pageview")
    suspend fun pageview(@Body beacon: PageViewBeacon): Response<Unit>
}

/** NavGraph route names and the backend's Android page allowlist (PAGE_ALLOWLIST). */
object PageviewDestinations {
    val ALLOWED: Set<String> = setOf(
        "search", "detail", "flagged", "environments", "sync", "upload",
        "settings", "about", "trends", "statistics", "recommendations"
    )

    /** Every route pattern declared in NavGraph; keep in sync when adding a destination. */
    val NAV_ROUTES: List<String> = listOf(
        "search",
        "detail/{memeId}?source={source}&searchId={searchId}",
        "flagged", "environments", "sync", "upload", "settings", "about", "trends", "statistics",
        "recommendations?q={query}"
    )

    /** `detail/{id}?x=y` -> `detail`. */
    fun strip(route: String): String = route.substringBefore('?').substringBefore('/')
}

/** Best-effort page-visit beacon: never blocks, never throws, stops for the session after a 404. */
class PageviewReporter(private val api: TelemetryApi, private val scope: CoroutineScope) {
    private val disabled = AtomicBoolean(false)

    fun report(destination: String) {
        if (disabled.get()) return
        val page = PageviewDestinations.strip(destination)
        if (page !in PageviewDestinations.ALLOWED) return
        scope.launch {
            try {
                if (disabled.get()) return@launch
                val response = api.pageview(PageViewBeacon(client = "android", page = page))
                if (response.code() == 404) disabled.set(true)
            } catch (e: CancellationException) {
                throw e
            } catch (_: Exception) {
                // best effort
            }
        }
    }
}
