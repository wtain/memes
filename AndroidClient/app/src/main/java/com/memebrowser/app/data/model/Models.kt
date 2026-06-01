package com.memebrowser.app.data.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable
data class Meme(
    val id: String,
    @SerialName("imageUrl") val imageUrl: String,
    @SerialName("originalFileName") val originalFileName: String? = null,
    val text: List<String>? = null,
    val tags: List<MemeTag>? = null,
    val excluded: Boolean? = null
)

@Serializable
data class MemeTag(
    val name: String,
    val category: String? = null,
    val score: Float? = null,
    val source: String? = null
)

@Serializable
data class MemeSearchResponse(
    val items: List<Meme>? = null,
    val facets: List<Facet>? = null,
    @SerialName("nextCursor") val nextCursor: String? = null,
    @SerialName("hasNext") val hasNext: Boolean? = null
)

@Serializable
data class Facet(
    val name: String,
    val buckets: List<FacetBucket>
)

@Serializable
data class FacetBucket(
    val value: String,
    val count: Float
)

@Serializable
data class HealthResponse(
    val status: String
)