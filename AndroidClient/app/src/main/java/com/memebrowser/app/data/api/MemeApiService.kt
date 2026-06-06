package com.memebrowser.app.data.api

import com.memebrowser.app.data.model.HealthResponse
import com.memebrowser.app.data.model.Meme
import com.memebrowser.app.data.model.MemeSearchResponse
import okhttp3.ResponseBody
import retrofit2.Response
import retrofit2.http.GET
import retrofit2.http.PUT
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

    @PUT("api/images/meme/{id}/mark_excluded")
    suspend fun markExcluded(@Path("id") id: String): Response<Unit>

    @PUT("api/images/meme/{id}/unmark_excluded")
    suspend fun unmarkExcluded(@Path("id") id: String): Response<Unit>

    @GET("health")
    suspend fun health(): HealthResponse

    @GET("api/images/excluded")
    suspend fun getExcluded(
        @Query("limit") limit: Int = 20,
        @Query("cursor") cursor: String? = null
    ): MemeSearchResponse

    @GET("api/images/{id}/similar")
    suspend fun getSimilarMemes(@Path("id") id: String): MemeSearchResponse

    @Streaming
    @GET("api/images/{id}")
    suspend fun downloadImage(@Path("id") id: String): ResponseBody
}