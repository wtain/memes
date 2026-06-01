package com.memebrowser.app.di

import android.content.Context
import com.jakewharton.retrofit2.converter.kotlinx.serialization.asConverterFactory
import com.memebrowser.app.data.api.MemeApiService
import com.memebrowser.app.data.api.UrlProvider
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.android.qualifiers.ApplicationContext
import dagger.hilt.components.SingletonComponent
import kotlinx.serialization.json.Json
import okhttp3.Cache
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Retrofit
import java.io.File
import java.util.concurrent.TimeUnit
import javax.inject.Singleton

@Module
@InstallIn(SingletonComponent::class)
object NetworkModule {

    private val json = Json {
        ignoreUnknownKeys = true
        coerceInputValues = true
    }

    @Provides
    @Singleton
    fun provideOkHttpClient(
        @ApplicationContext context: Context,
        urlProvider: UrlProvider
    ): OkHttpClient {
        val cache = Cache(File(context.cacheDir, "okhttp"), 50L * 1024 * 1024)
        return OkHttpClient.Builder()
            .cache(cache)
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(30, TimeUnit.SECONDS)
            // Rewrite the base URL for every request so environment switches take effect immediately
            .addInterceptor { chain ->
                val original = chain.request()
                val newBase = urlProvider.current().toHttpUrl()
                val newUrl = original.url.newBuilder()
                    .scheme(newBase.scheme)
                    .host(newBase.host)
                    .port(newBase.port)
                    .build()
                chain.proceed(original.newBuilder().url(newUrl).build())
            }
            .addInterceptor(HttpLoggingInterceptor().apply {
                level = HttpLoggingInterceptor.Level.BASIC
            })
            .build()
    }

    @Provides
    @Singleton
    fun provideRetrofit(okHttpClient: OkHttpClient): Retrofit {
        // Base URL is a placeholder; the interceptor above rewrites it per-request
        return Retrofit.Builder()
            .baseUrl("http://localhost/")
            .client(okHttpClient)
            .addConverterFactory(json.asConverterFactory("application/json".toMediaType()))
            .build()
    }

    @Provides
    @Singleton
    fun provideMemeApiService(retrofit: Retrofit): MemeApiService =
        retrofit.create(MemeApiService::class.java)
}