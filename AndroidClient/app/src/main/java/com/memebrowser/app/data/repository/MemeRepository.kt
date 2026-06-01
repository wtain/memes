package com.memebrowser.app.data.repository

import com.memebrowser.app.data.api.MemeApiService
import com.memebrowser.app.data.model.HealthResponse
import com.memebrowser.app.data.model.Meme
import com.memebrowser.app.data.model.MemeSearchResponse
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.ResponseBody
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class MemeRepository @Inject constructor(
    private val api: MemeApiService,
    private val okHttpClient: OkHttpClient
) {
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

    suspend fun downloadImage(id: String): Result<ResponseBody> = runCatching {
        api.downloadImage(id)
    }
}