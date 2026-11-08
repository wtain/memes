package com.memebrowser.app.di

import android.content.Context
import com.memebrowser.app.data.album.AlbumAccess
import com.memebrowser.app.data.album.AlbumRowSource
import com.memebrowser.app.data.album.AlbumScanner
import com.memebrowser.app.data.album.ContentResolverAlbumAccess
import com.memebrowser.app.data.album.ContentResolverAlbumRowSource
import com.memebrowser.app.data.album.EnvironmentIdSource
import com.memebrowser.app.data.album.SafAlbumScanner
import com.memebrowser.app.data.album.SyncGate
import com.memebrowser.app.data.store.EnvironmentStore
import com.memebrowser.app.data.sync.EngineSyncGate
import com.memebrowser.app.data.sync.SyncEngine
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

    /** The real gate (A3): an album change stops the environment's sync before the wipe. */
    @Provides
    @Singleton
    fun provideSyncGate(engine: SyncEngine): SyncGate = EngineSyncGate(engine)

    @Provides
    fun provideEnvironmentIdSource(store: EnvironmentStore): EnvironmentIdSource =
        EnvironmentIdSource { store.environments.first().map { it.id } }
}
