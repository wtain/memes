package com.memebrowser.app.data.api

import com.memebrowser.app.data.model.HealthResponse
import com.memebrowser.app.data.model.ImageDescription
import com.memebrowser.app.data.model.Meme
import com.memebrowser.app.data.model.MemeSearchResponse
import com.memebrowser.app.data.model.StatisticsResponse
import com.memebrowser.app.data.model.TrendEntry
import com.memebrowser.app.data.model.TrendsRun
import com.memebrowser.app.data.model.UploadResponse
import okhttp3.MultipartBody
import okhttp3.ResponseBody
import retrofit2.Response
import retrofit2.http.GET
import retrofit2.http.Multipart
import retrofit2.http.POST
import retrofit2.http.PUT
import retrofit2.http.Part
import retrofit2.http.Path
import retrofit2.http.Query
import retrofit2.http.Streaming

interface MemeApiService {

    @GET("api/images")
    suspend fun searchMemes(
        @Query("q") query: String? = null,
        @Query("limit") limit: Int = 20,
        @Query("facets") facets: String? = null,
        @Query("cursor") cursor: String? = null
    ): MemeSearchResponse

    @GET("api/images/meme/{id}")
    suspend fun getMeme(@Path("id") id: String): Meme

    @PUT("api/images/meme/{id}/mark_flagged")
    suspend fun markFlagged(@Path("id") id: String): Response<Unit>

    @PUT("api/images/meme/{id}/unmark_flagged")
    suspend fun unmarkFlagged(@Path("id") id: String): Response<Unit>

    @GET("health")
    suspend fun health(): HealthResponse

    @GET("api/images/flagged")
    suspend fun getFlagged(
        @Query("limit") limit: Int = 20,
        @Query("cursor") cursor: String? = null
    ): MemeSearchResponse

    @GET("api/images/{id}/similar")
    suspend fun getSimilarMemes(@Path("id") id: String): MemeSearchResponse

    @GET("api/images/{id}/descriptions")
    suspend fun getDescriptions(@Path("id") id: String): List<ImageDescription>

    @Streaming
    @GET("api/images/{id}")
    suspend fun downloadImage(@Path("id") id: String): ResponseBody

    @Multipart
    @POST("api/uploads")
    suspend fun uploadImages(@Part files: List<MultipartBody.Part>): UploadResponse

    @GET("api/trends/dates")
    suspend fun getTrendsDates(): List<String>

    @GET("api/trends/dates/{date}/runs/latest")
    suspend fun getLatestTrendsRun(@Path("date") date: String): TrendsRun

    @GET("api/trends/runs/{runId}")
    suspend fun getTrendsEntries(@Path("runId") runId: String): List<TrendEntry>

    @GET("api/recommendations")
    suspend fun getRecommendations(
        @Query("q") query: String? = null,
        @Query("limit") limit: Int = 20,
        @Query("cursor") cursor: String? = null
    ): MemeSearchResponse

    @GET("api/diagnostics/statistics")
    suspend fun getStatistics(): StatisticsResponse
}