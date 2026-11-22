package com.memebrowser.app.di

import android.content.Context
import com.jakewharton.retrofit2.converter.kotlinx.serialization.asConverterFactory
import com.memebrowser.app.data.api.MemeApiService
import com.memebrowser.app.data.repository.AlbumFileSource
import com.memebrowser.app.data.repository.BatchUploader
import com.memebrowser.app.data.repository.RetrofitBatchUploader
import com.memebrowser.app.data.store.EnvironmentStore
import com.memebrowser.app.data.sync.ContentResolverAlbumFileSource
import com.memebrowser.app.data.sync.DispatchingAlbumFileSource
import com.memebrowser.app.data.sync.AlwaysOnline
import com.memebrowser.app.data.sync.MediaStoreAlbumFileSource
import com.memebrowser.app.data.sync.NetworkGate
import com.memebrowser.app.data.sync.NoServiceStarter
import com.memebrowser.app.data.sync.SyncServiceStarter
import com.memebrowser.app.data.sync.SyncScope
import com.memebrowser.app.data.sync.SyncSettingsSource
import com.memebrowser.app.data.sync.TrackerSyncSettings
import dagger.Binds
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.android.qualifiers.ApplicationContext
import dagger.hilt.components.SingletonComponent
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.first
import kotlinx.serialization.json.Json
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Retrofit
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.TimeUnit
import javax.inject.Singleton

@Module
@InstallIn(SingletonComponent::class)
abstract class SyncBindingsModule {
    @Binds
    abstract fun bindSyncSettings(impl: TrackerSyncSettings): SyncSettingsSource
}

@Module
@InstallIn(SingletonComponent::class)
object SyncModule {

    @Provides
    @Singleton
    @SyncScope
    fun provideSyncScope(): CoroutineScope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    /** No network checks yet: A6 Task 6 replaces this with the ConnectivityManager-backed gate. */
    @Provides
    @Singleton
    fun provideNetworkGate(): NetworkGate = AlwaysOnline

    /** Starts nothing yet: A6 Task 6 replaces this with the foreground-service starter. */
    @Provides
    @Singleton
    fun provideSyncServiceStarter(): SyncServiceStarter = NoServiceStarter

    @Provides
    @Singleton
    fun provideAlbumFileSource(@ApplicationContext context: Context): AlbumFileSource =
        DispatchingAlbumFileSource(
            saf = ContentResolverAlbumFileSource(context.contentResolver),
            media = MediaStoreAlbumFileSource(context.contentResolver),
        )

    /**
     * Uploads use their own client and one Retrofit per environment base URL, not the app-wide client: that one
     * rewrites every request to the CURRENTLY selected environment (wrong for a sync of another environment) and has
     * a 30 s read timeout, too short while the server checks up to 50 files of 20 MB. Here: 10 s connect, 2 min per
     * write, 5 min to wait for the answer, no overall call limit (a batch can legitimately take minutes).
     */
    @Provides
    @Singleton
    fun provideBatchUploader(source: AlbumFileSource, store: EnvironmentStore): BatchUploader {
        val json = Json { ignoreUnknownKeys = true; coerceInputValues = true }
        val client = OkHttpClient.Builder()
            .connectTimeout(10, TimeUnit.SECONDS)
            .writeTimeout(2, TimeUnit.MINUTES)
            .readTimeout(5, TimeUnit.MINUTES)
            .callTimeout(0, TimeUnit.MILLISECONDS)
            .addInterceptor(HttpLoggingInterceptor().apply { level = HttpLoggingInterceptor.Level.BASIC })
            .build()
        val apis = ConcurrentHashMap<String, MemeApiService>()
        return RetrofitBatchUploader(source) { environmentId ->
            val baseUrl = store.environments.first().find { it.id == environmentId }?.baseUrl
                ?: return@RetrofitBatchUploader null
            val normalized = baseUrl.trim().trimEnd('/') + "/"
            normalized.toHttpUrl() // throws IllegalArgumentException for an invalid URL (becomes Unexpected)
            apis.getOrPut(normalized) {
                Retrofit.Builder()
                    .baseUrl(normalized)
                    .client(client)
                    .addConverterFactory(json.asConverterFactory("application/json".toMediaType()))
                    .build()
                    .create(MemeApiService::class.java)
            }
        }
    }
}
