package com.memebrowser.app.data.repository

import android.util.Log
import com.memebrowser.app.data.api.MemeApiService
import com.memebrowser.app.data.model.HealthResponse
import com.memebrowser.app.data.model.Meme
import com.memebrowser.app.data.model.MemeSearchResponse
import com.memebrowser.app.data.model.TrendEntry
import com.memebrowser.app.data.model.TrendsRun
import com.memebrowser.app.data.model.UploadResponse
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.ResponseBody
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
open class MemeRepository @Inject constructor(
    private val api: MemeApiService,
    private val okHttpClient: OkHttpClient
) {
    companion object {
        private const val TAG = "MemeRepository"
    }

    suspend fun search(
        query: String?,
        facets: String?,
        cursor: String?,
        limit: Int = 20
    ): Result<MemeSearchResponse> = runCatching {
        api.searchMemes(
            query = query?.takeIf { it.isNotBlank() },
            facets = facets?.takeIf { it.isNotBlank() },
            cursor = cursor,
            limit = limit
        )
    }

    suspend fun getMeme(id: String): Result<Meme> = runCatching {
        api.getMeme(id)
    }

    suspend fun markExcluded(id: String): Result<Unit> = runCatching {
        val response = api.markExcluded(id)
        if (!response.isSuccessful) error("HTTP ${response.code()}")
    }

    suspend fun unmarkExcluded(id: String): Result<Unit> = runCatching {
        val response = api.unmarkExcluded(id)
        if (!response.isSuccessful) error("HTTP ${response.code()}")
    }

    suspend fun health(): Result<HealthResponse> = runCatching {
        api.health()
    }

    suspend fun healthCheck(baseUrl: String): Result<Unit> = runCatching {
        val url = baseUrl.trimEnd('/') + "/health"
        val request = Request.Builder().url(url).get().build()
        okHttpClient.newCall(request).execute().use { response ->
            if (!response.isSuccessful) error("HTTP ${response.code}")
        }
    }

    suspend fun getExcluded(cursor: String?, limit: Int = 20): Result<MemeSearchResponse> = runCatching {
        api.getExcluded(limit = limit, cursor = cursor)
    }

    suspend fun getSimilarMemes(id: String): Result<List<Meme>> = runCatching {
        api.getSimilarMemes(id).items ?: emptyList()
    }

    suspend fun uploadImages(parts: List<MultipartBody.Part>): Result<UploadResponse> = runCatching {
        api.uploadImages(parts)
    }

    suspend fun getTrendsDates(): Result<List<String>> = runCatching {
        api.getTrendsDates()
    }

    suspend fun getLatestTrendsRun(date: String): Result<TrendsRun> = runCatching {
        api.getLatestTrendsRun(date)
    }

    suspend fun getTrendsEntries(runId: String): Result<List<TrendEntry>> = runCatching {
        api.getTrendsEntries(runId)
    }

    suspend fun getRecommendations(query: String?, cursor: String?, limit: Int = 20): Result<MemeSearchResponse> = runCatching {
        api.getRecommendations(query = query, limit = limit, cursor = cursor)
    }

    suspend fun downloadImage(id: String): Result<ResponseBody> {
        Log.d(TAG, "downloadImage: requesting id=$id")
        return runCatching { api.downloadImage(id) }
            .onSuccess { body ->
                Log.d(TAG, "downloadImage: ok id=$id contentType=${body.contentType()} contentLength=${body.contentLength()}")
            }
            .onFailure { e ->
                Log.e(TAG, "downloadImage: failed id=$id", e)
            }
    }
}