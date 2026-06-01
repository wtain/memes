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
    excluded = false
)

val androidFakeMemeExcluded = androidFakeMeme.copy(id = "meme-2", excluded = true)

val androidFakeSearchResponse = MemeSearchResponse(
    items = listOf(androidFakeMeme, androidFakeMemeExcluded),
    facets = emptyList(),
    nextCursor = null,
    hasNext = false
)

val androidFakeHealthy = HealthResponse("healthy")