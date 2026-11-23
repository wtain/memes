package com.memebrowser.app.data.tracker

import androidx.room.testing.MigrationTestHelper
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * A7: a version 1 tracker database (from the exported 1.json) opens as version 2 with every row and state kept and a NULL
 * answer. Needs a device or emulator.
 */
@RunWith(AndroidJUnit4::class)
class TrackerMigrationTest {

    private val dbName = "tracker-migration-test.db"

    @get:Rule
    val helper = MigrationTestHelper(InstrumentationRegistry.getInstrumentation(), TrackerDatabase::class.java)

    @Test
    fun migrate1To2KeepsRowsAndLeavesTheAnswerNull() {
        helper.createDatabase(dbName, 1).use { db ->
            db.execSQL(
                "INSERT INTO tracked_files (environmentId, albumTreeUri, documentId, displayName, mimeType, sizeBytes, lastModified, " +
                    "state, rejectionReason, uploadedAt, updatedAt, presentInAlbum, lastSeenScan) VALUES " +
                    "('builtin-general', 'content://tree/album', 'up', 'up.jpg', 'image/jpeg', 10, 1, 'UPLOADED', NULL, 5, 5, 1, 1), " +
                    "('builtin-general', 'content://tree/album', 'pend', 'pend.jpg', 'image/jpeg', 10, 2, 'PENDING', NULL, NULL, 5, 1, 1)"
            )
        }

        helper.runMigrationsAndValidate(dbName, 2, true, MIGRATION_1_2).use { db ->
            db.query("SELECT documentId, state, uploadedAt, uploadAnswer FROM tracked_files ORDER BY documentId").use { c ->
                assertEquals(2, c.count)
                assertTrue(c.moveToNext())
                assertEquals("pend", c.getString(0))
                assertEquals("PENDING", c.getString(1))
                assertTrue(c.isNull(2))
                assertTrue(c.isNull(3))
                assertTrue(c.moveToNext())
                assertEquals("up", c.getString(0))
                assertEquals("UPLOADED", c.getString(1))
                assertEquals(5L, c.getLong(2))
                assertTrue(c.isNull(3))
            }
        }
    }
}
