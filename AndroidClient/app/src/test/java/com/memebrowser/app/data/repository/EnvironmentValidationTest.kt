package com.memebrowser.app.data.repository

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class EnvironmentValidationTest {

    @Test
    fun `existing default names are valid`() {
        assertTrue(isValidCollectionName("General"))
        assertTrue(isValidCollectionName("IT"))
        assertTrue(isValidCollectionName("Metal"))
    }

    @Test
    fun `names with spaces hyphens and underscores are valid`() {
        assertTrue(isValidCollectionName("My Collection"))
        assertTrue(isValidCollectionName("work-photos"))
        assertTrue(isValidCollectionName("photos_2024"))
        assertTrue(isValidCollectionName("A1 B2-C3_D4"))
    }

    @Test
    fun `single character alphanumeric names are valid`() {
        assertTrue(isValidCollectionName("A"))
        assertTrue(isValidCollectionName("1"))
    }

    @Test
    fun `blank and empty names are rejected`() {
        assertFalse(isValidCollectionName(""))
        assertFalse(isValidCollectionName("   "))
    }

    @Test
    fun `names with path separators are rejected`() {
        assertFalse(isValidCollectionName("/hack"))
        assertFalse(isValidCollectionName("a/b"))
        assertFalse(isValidCollectionName("a\\b"))
        assertFalse(isValidCollectionName("../../etc"))
    }

    @Test
    fun `names starting with special characters are rejected`() {
        assertFalse(isValidCollectionName("-dash-first"))
        assertFalse(isValidCollectionName("_underscore-first"))
        assertFalse(isValidCollectionName(" space-first"))
    }

    @Test
    fun `names with disallowed characters are rejected`() {
        assertFalse(isValidCollectionName("my@collection"))
        assertFalse(isValidCollectionName("my:collection"))
        assertFalse(isValidCollectionName("my*collection"))
        assertFalse(isValidCollectionName("my?collection"))
        assertFalse(isValidCollectionName("my<collection>"))
        assertFalse(isValidCollectionName("my|collection"))
        assertFalse(isValidCollectionName("my.collection"))
        assertFalse(isValidCollectionName("my!collection"))
    }
}