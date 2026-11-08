package com.memebrowser.app.data.tracker

import androidx.room.Database
import androidx.room.RoomDatabase

@Database(entities = [TrackedFileEntity::class], version = 1, exportSchema = true)
abstract class TrackerDatabase : RoomDatabase() {
    abstract fun trackerDao(): TrackerDao
}
