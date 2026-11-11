package com.memebrowser.app.data.settings

import androidx.datastore.preferences.core.PreferenceDataStoreFactory
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

class AppSettingsStoreTest {

    @get:Rule
    val tmp = TemporaryFolder()

    private fun store() = AppSettingsStore(
        PreferenceDataStoreFactory.create(produceFile = { File(tmp.root, "s.preferences_pb") })
    )

    @Test
    fun `defaults - startup check on, 20 per page, 160 dp, nothing skipped`() = runTest {
        val s = store()
        assertTrue(s.checkUpdatesOnStartup.first())
        assertEquals(20, s.resultsPerPage.first())
        assertEquals(160, s.gridMinSizeDp.first())
        assertNull(s.skippedUpdateTag.first())
    }

    @Test
    fun `startup check can be switched off and on`() = runTest {
        val s = store()
        s.setCheckUpdatesOnStartup(false)
        assertEquals(false, s.checkUpdatesOnStartup.first())
        s.setCheckUpdatesOnStartup(true)
        assertEquals(true, s.checkUpdatesOnStartup.first())
    }

    @Test
    fun `numeric settings are clamped to their range`() = runTest {
        val s = store()
        s.setResultsPerPage(1_000)
        s.setGridMinSizeDp(5)
        assertEquals(AppSettingsStore.MAX_RESULTS_PER_PAGE, s.resultsPerPage.first())
        assertEquals(AppSettingsStore.MIN_GRID_MIN_SIZE_DP, s.gridMinSizeDp.first())
    }

    @Test
    fun `skipped tag is remembered`() = runTest {
        val s = store()
        s.setSkippedUpdateTag("android-v1.2.3")
        assertEquals("android-v1.2.3", s.skippedUpdateTag.first())
    }
}
