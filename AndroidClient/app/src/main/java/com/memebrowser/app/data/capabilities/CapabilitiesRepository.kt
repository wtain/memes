package com.memebrowser.app.data.capabilities

import com.memebrowser.app.data.api.MemeApiService
import com.memebrowser.app.data.api.UrlProvider
import com.memebrowser.app.data.model.CapabilitiesResponse
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.transformLatest
import retrofit2.HttpException
import java.util.concurrent.ConcurrentHashMap
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Reads the capabilities of the selected backend once per base URL and keeps the answer in memory
 * for the session. Failure tolerant: a 404 (old backend without the endpoint) is remembered as
 * "show everything"; a network or other error shows everything now and is retried on the next load.
 */
@Singleton
class CapabilitiesRepository @Inject constructor(
    private val api: MemeApiService,
    private val urlProvider: UrlProvider
) {
    private val cache = ConcurrentHashMap<String, FeatureVisibility>()

    /** Emits [FeatureVisibility.ALL] (or the cached answer) at once, then the loaded one, per backend switch. */
    @OptIn(ExperimentalCoroutinesApi::class)
    val visibility: Flow<FeatureVisibility> = urlProvider.baseUrl.transformLatest { url ->
        val cached = cache[url]
        if (cached != null) {
            emit(cached)
        } else {
            emit(FeatureVisibility.ALL)
            val loaded = load(url)
            if (loaded !== FeatureVisibility.ALL) emit(loaded)
        }
    }

    private suspend fun load(url: String): FeatureVisibility {
        cache[url]?.let { return it }
        val response: CapabilitiesResponse = try {
            api.getCapabilities()
        } catch (e: CancellationException) {
            throw e
        } catch (e: HttpException) {
            if (e.code() == 404 && urlProvider.current() == url) cache[url] = FeatureVisibility.ALL
            return FeatureVisibility.ALL
        } catch (e: Exception) {
            return FeatureVisibility.ALL
        }
        val result = FeatureVisibility(response)
        // The request went to whichever backend was current when it ran; only keep it if that is still [url].
        if (urlProvider.current() == url) cache[url] = result
        return result
    }
}
