package com.memebrowser.app.data.album

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Test

class MediaAccessTest {
    private val images = "android.permission.READ_MEDIA_IMAGES"
    private val selected = "android.permission.READ_MEDIA_VISUAL_USER_SELECTED"
    private val external = "android.permission.READ_EXTERNAL_STORAGE"

    @Test
    fun `sdk 34 full`() = assertEquals(MediaAccess.FULL, MediaAccesses.resolve(setOf(images, selected), 34, true))

    @Test
    fun `sdk 34 partial`() = assertEquals(MediaAccess.PARTIAL, MediaAccesses.resolve(setOf(selected), 34, true))

    @Test
    fun `sdk 33 full`() = assertEquals(MediaAccess.FULL, MediaAccesses.resolve(setOf(images), 33, true))

    @Test
    fun `sdk 31 full`() = assertEquals(MediaAccess.FULL, MediaAccesses.resolve(setOf(external), 31, true))

    @Test
    fun `none granted`() {
        assertEquals(MediaAccess.DENIED, MediaAccesses.resolve(emptySet(), 34, true))
        assertEquals(MediaAccess.BLOCKED, MediaAccesses.resolve(emptySet(), 31, false))
    }

    @Test
    fun `permissions to request`() {
        assertArrayEquals(arrayOf(images, selected), MediaAccesses.permissionsToRequest(34))
        assertArrayEquals(arrayOf(images), MediaAccesses.permissionsToRequest(33))
        assertArrayEquals(arrayOf(external), MediaAccesses.permissionsToRequest(29))
        assertArrayEquals(arrayOf(external), MediaAccesses.permissionsToRequest(32))
    }
}
