package com.memebrowser.app.data.tracker

import androidx.room.Database
import androidx.room.RoomDatabase
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase

@Database(entities = [TrackedFileEntity::class], version = 2, exportSchema = true)
abstract class TrackerDatabase : RoomDatabase() {
    abstract fun trackerDao(): TrackerDao
}

/** A7: adds the server's answer per uploaded file; existing rows keep everything and get NULL (unclassified). */
val MIGRATION_1_2 = object : Migration(1, 2) {
    override fun migrate(db: SupportSQLiteDatabase) {
        db.execSQL("ALTER TABLE tracked_files ADD COLUMN uploadAnswer TEXT")
    }
}
