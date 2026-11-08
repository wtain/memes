package com.memebrowser.app.di

import android.content.Context
import androidx.room.Room
import com.memebrowser.app.data.tracker.TrackerDao
import com.memebrowser.app.data.tracker.TrackerDatabase
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.android.qualifiers.ApplicationContext
import dagger.hilt.components.SingletonComponent
import javax.inject.Singleton

@Module
@InstallIn(SingletonComponent::class)
object TrackerModule {

    @Provides
    @Singleton
    fun provideTrackerDatabase(@ApplicationContext context: Context): TrackerDatabase =
        Room.databaseBuilder(context, TrackerDatabase::class.java, "tracker.db").build()

    @Provides
    fun provideTrackerDao(db: TrackerDatabase): TrackerDao = db.trackerDao()
}
