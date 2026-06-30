package com.memebrowser.app.util

import com.memebrowser.app.data.model.HealthResponse
import com.memebrowser.app.data.model.Meme
import com.memebrowser.app.data.model.MemeSearchResponse

val androidFakeMeme = Meme(
    id = "meme-1",
    imageUrl = "http://localhost/api/images/meme-1",
    originalFileName = "funny.jpg",
    text = listOf("hello world"),
    tags = null,
    flagged = false
)

val androidFakeMeme2 = androidFakeMeme.copy(id = "meme-2", flagged = true)

val androidFakeSearchResponse = MemeSearchResponse(
    items = listOf(androidFakeMeme, androidFakeMeme2),
    facets = emptyList(),
    nextCursor = null,
    hasNext = false
)

val androidFakeHealthy = HealthResponse("healthy")