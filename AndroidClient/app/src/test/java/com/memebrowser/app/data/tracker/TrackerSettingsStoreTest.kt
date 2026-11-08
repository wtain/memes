package com.memebrowser.app.data.tracker

import androidx.datastore.preferences.core.PreferenceDataStoreFactory
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

class TrackerSettingsStoreTest {

    @get:Rule
    val tmp = TemporaryFolder()

    private fun file() = File(tmp.root, "s.preferences_pb")
    private fun store() = TrackerSettingsStore(PreferenceDataStoreFactory.create(produceFile = ::file))

    @Test
    fun `album is null until selected, then per environment`() = runTest {
        val s = store()
        assertNull(s.album("builtin-general").first())
        s.setAlbum("builtin-general", "content://a", "A")
        s.setAlbum("builtin-it", "content://b", "B")
        assertEquals(AlbumSelection("content://a", "A"), s.album("builtin-general").first())
        assertEquals(AlbumSelection("content://b", "B"), s.album("builtin-it").first())
        assertNull(s.album("builtin-metal").first())
    }

    @Test
    fun `clearAlbum removes only that environment's album`() = runTest {
        val s = store()
        s.setAlbum("e1", "content://a", "A")
        s.setAlbum("e2", "content://b", "B")
        s.clearAlbum("e1")
        assertNull(s.album("e1").first())
        assertEquals("B", s.album("e2").first()?.displayName)
    }

    @Test
    fun `settings survive a restart (new store over the same file)`() = runTest {
        // Two DataStore instances over one file are not allowed, so write, then read with a plain reload of the file contents.
        val first = PreferenceDataStoreFactory.create(produceFile = ::file)
        TrackerSettingsStore(first).apply {
            setAlbum("e1", "content://a", "A")
            setBatchSize(12)
        }
        val raw = file().readBytes()
        val copy = File(tmp.root, "copy.preferences_pb").also { it.writeBytes(raw) }
        val reopened = TrackerSettingsStore(PreferenceDataStoreFactory.create(produceFile = { copy }))
        assertEquals(AlbumSelection("content://a", "A"), reopened.album("e1").first())
        assertEquals(12, reopened.batchSize.first())
    }

    @Test
    fun `batch size defaults to 50 and is clamped to 1 to 50`() = runTest {
        val s = store()
        assertEquals(50, s.batchSize.first())
        s.setBatchSize(0)
        assertEquals(1, s.batchSize.first())
        s.setBatchSize(-5)
        assertEquals(1, s.batchSize.first())
        s.setBatchSize(51)
        assertEquals(50, s.batchSize.first())
        s.setBatchSize(25)
        assertEquals(25, s.batchSize.first())
    }

    @Test
    fun `clampBatchSize bounds`() {
        assertEquals(1, TrackerSettingsStore.clampBatchSize(Int.MIN_VALUE))
        assertEquals(50, TrackerSettingsStore.clampBatchSize(Int.MAX_VALUE))
    }
}
