package com.memebrowser.app.di

import android.content.Context
import com.memebrowser.app.data.album.AlbumAccess
import com.memebrowser.app.data.album.AlbumRowSource
import com.memebrowser.app.data.album.AlbumScanner
import com.memebrowser.app.data.album.ContentResolverAlbumAccess
import com.memebrowser.app.data.album.ContentResolverAlbumRowSource
import com.memebrowser.app.data.album.EnvironmentIdSource
import com.memebrowser.app.data.album.NoOpSyncGate
import com.memebrowser.app.data.album.SafAlbumScanner
import com.memebrowser.app.data.album.SyncGate
import com.memebrowser.app.data.store.EnvironmentStore
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.android.qualifiers.ApplicationContext
import dagger.hilt.components.SingletonComponent
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import javax.inject.Singleton

@Module
@InstallIn(SingletonComponent::class)
object AlbumModule {

    @Provides
    @Singleton
    fun provideAlbumAccess(@ApplicationContext context: Context): AlbumAccess =
        ContentResolverAlbumAccess(context.contentResolver)

    @Provides
    fun provideAlbumRowSource(@ApplicationContext context: Context): AlbumRowSource =
        ContentResolverAlbumRowSource(context.contentResolver)

    @Provides
    @Singleton
    fun provideAlbumScanner(source: AlbumRowSource, access: AlbumAccess): AlbumScanner =
        SafAlbumScanner(source, access, Dispatchers.IO)

    /** A3 replaces this binding with the real upload-job gate; until then there is never a sync to stop. */
    @Provides
    @Singleton
    fun provideSyncGate(): SyncGate = NoOpSyncGate

    @Provides
    fun provideEnvironmentIdSource(store: EnvironmentStore): EnvironmentIdSource =
        EnvironmentIdSource { store.environments.first().map { it.id } }
}
