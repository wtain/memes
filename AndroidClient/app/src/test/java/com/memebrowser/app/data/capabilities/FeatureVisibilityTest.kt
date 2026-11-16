package com.memebrowser.app.data.capabilities

import com.memebrowser.app.data.model.CapabilitiesFeatures
import com.memebrowser.app.data.model.CapabilitiesResponse
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class FeatureVisibilityTest {

    private fun features(all: Boolean = true, block: (CapabilitiesFeatures) -> CapabilitiesFeatures = { it }) =
        block(CapabilitiesFeatures(all, all, all, all, all, all, all, all, all))

    private fun caps(stage: String, f: CapabilitiesFeatures) = CapabilitiesResponse(stage, "general", f)

    private fun assertAll(v: FeatureVisibility, expected: Boolean) {
        assertEquals(expected, v.showFlagged)
        assertEquals(expected, v.showFlagToggle)
        assertEquals(expected, v.showDescriptionFeedback)
        assertEquals(expected, v.showHistory)
        assertEquals(expected, v.showTrends)
        assertEquals(expected, v.showStatistics)
        assertEquals(expected, v.showComics)
        assertEquals(expected, v.showAlbumSync)
    }

    @Test
    fun `no capabilities shows everything`() = assertAll(FeatureVisibility(null), true)

    @Test
    fun `ALL shows everything`() = assertAll(FeatureVisibility.ALL, true)

    @Test
    fun `dev with all flags shows everything`() = assertAll(FeatureVisibility(caps("dev", features())), true)

    @Test
    fun `prod hides everything even if flags are true`() = assertAll(FeatureVisibility(caps("prod", features())), false)

    @Test
    fun `prod with all flags false hides everything`() = assertAll(FeatureVisibility(caps("prod", features(false))), false)

    @Test
    fun `dev with a flag false hides only the matching entries`() {
        val v = FeatureVisibility(caps("dev", features { it.copy(trends = false) }))
        assertFalse(v.showTrends)
        assertTrue(v.showStatistics)
        assertTrue(v.showFlagged)
        assertTrue(v.showAlbumSync)
    }

    @Test
    fun `each flag drives its own entries`() {
        val noAdmin = FeatureVisibility(caps("dev", features { it.copy(admin = false) }))
        assertFalse(noAdmin.showFlagged)
        assertFalse(noAdmin.showFlagToggle)
        assertFalse(noAdmin.showDescriptionFeedback)
        assertFalse(FeatureVisibility(caps("dev", features { it.copy(history = false) })).showHistory)
        assertFalse(FeatureVisibility(caps("dev", features { it.copy(statistics = false) })).showStatistics)
        assertFalse(FeatureVisibility(caps("dev", features { it.copy(comics = false) })).showComics)
        assertFalse(FeatureVisibility(caps("dev", features { it.copy(uploads = false) })).showAlbumSync)
    }

    @Test
    fun `unknown stage is treated as not prod`() = assertAll(FeatureVisibility(caps("staging", features())), true)
}
