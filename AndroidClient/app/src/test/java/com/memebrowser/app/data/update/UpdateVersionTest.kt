package com.memebrowser.app.data.update

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class UpdateVersionTest {

    private fun release(tag: String, asset: String = "app.apk") =
        GitHubRelease(tag, listOf(GitHubAsset(asset, "https://example/$asset", "application/octet-stream")))

    @Test
    fun `newer patch, minor and major are newer`() {
        assertTrue(isNewer("android-v1.0.1", "android-v1.0.0"))
        assertTrue(isNewer("android-v1.1.0", "android-v1.0.9"))
        assertTrue(isNewer("android-v2.0.0", "android-v1.9.9"))
    }

    @Test
    fun `same or older is not newer`() {
        assertFalse(isNewer("android-v1.0.0", "android-v1.0.0"))
        assertFalse(isNewer("android-v1.0.0", "android-v1.0.1"))
    }

    @Test
    fun `a non-release current version (dev build) counts as outdated`() {
        assertTrue(isNewer("android-v1.0.0", "dev"))
    }

    @Test
    fun `no release or no apk means up to date`() {
        assertEquals(UpdateStatus.UpToDate, statusFor(null, "android-v1.0.0"))
        assertEquals(UpdateStatus.UpToDate, statusFor(release("android-v2.0.0", "notes.txt"), "android-v1.0.0"))
    }

    @Test
    fun `newer release with an apk is available`() {
        assertEquals(
            UpdateStatus.Available("android-v2.0.0", "https://example/app.apk"),
            statusFor(release("android-v2.0.0"), "android-v1.0.0")
        )
    }
}
