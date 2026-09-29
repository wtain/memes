package com.memebrowser.app

import android.app.Application
import coil.ImageLoader
import coil.ImageLoaderFactory
import com.memebrowser.app.data.repository.EnvironmentRepository
import com.memebrowser.app.util.CrashHandler
import dagger.hilt.android.HiltAndroidApp
import kotlinx.coroutines.runBlocking
import okhttp3.OkHttpClient
import javax.inject.Inject

@HiltAndroidApp
class MemeBrowserApp : Application(), ImageLoaderFactory {

    @Inject lateinit var okHttpClient: OkHttpClient
    @Inject lateinit var environmentRepository: EnvironmentRepository

    override fun onCreate() {
        super.onCreate()
        CrashHandler.install(this)
        // Must finish before any screen or network call is created, so a plain blocking read
        // of the (small, local) DataStore preference is used rather than a fire-and-forget
        // coroutine that could lose the race with the first request.
        runBlocking { environmentRepository.restoreSelectedBaseUrl() }
    }

    override fun newImageLoader(): ImageLoader =
        ImageLoader.Builder(this)
            .okHttpClient(okHttpClient)
            .crossfade(true)
            .build()
}