package com.memebrowser.app.data.repository

/**
 * A valid collection name may contain letters, digits, spaces, hyphens, and underscores,
 * and must start with a letter or digit. This keeps names safe for use as Android
 * MediaStore RELATIVE_PATH directory segments.
 */
fun isValidCollectionName(name: String): Boolean =
    name.isNotBlank() && name.matches(Regex("[a-zA-Z0-9][a-zA-Z0-9 _\\-]*"))