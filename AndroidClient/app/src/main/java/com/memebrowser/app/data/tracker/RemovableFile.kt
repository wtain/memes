package com.memebrowser.app.data.tracker

/** An uploaded file that may be removed from the phone: its id in the album and the epoch-ms instant it was uploaded. */
data class RemovableFile(val documentId: String, val uploadedAt: Long)
