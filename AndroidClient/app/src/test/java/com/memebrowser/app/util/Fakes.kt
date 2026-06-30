package com.memebrowser.app.util

import com.memebrowser.app.data.model.Meme
import com.memebrowser.app.data.model.MemeSearchResponse

val fakeMeme = Meme(
    id = "meme-1",
    imageUrl = "http://localhost/api/images/meme-1",
    originalFileName = "funny.jpg",
    text = listOf("hello world"),
    tags = null,
    flagged = false
)

val fakeMeme2 = fakeMeme.copy(id = "meme-2", flagged = true)

val fakeSearchPage1 = MemeSearchResponse(
    items = listOf(fakeMeme, fakeMeme2),
    facets = emptyList(),
    nextCursor = "cursor-abc",
    hasNext = true
)

val fakeSearchPage2 = MemeSearchResponse(
    items = listOf(fakeMeme.copy(id = "meme-3")),
    facets = emptyList(),
    nextCursor = null,
    hasNext = false
)

val fakeSearchEmpty = MemeSearchResponse(
    items = emptyList(),
    facets = emptyList(),
    nextCursor = null,
    hasNext = false
)