// GENERATED — do not edit by hand.
// Source: shared/schemas/*.schema.json
// Regenerate: python AndroidClient/scripts/generate_dtos.py

package com.memebrowser.app.data.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable


@Serializable
data class Concept(
    @SerialName("id") val id: Int,
    @SerialName("name") val name: String
)

@Serializable
data class FacetBucket(
    @SerialName("value") val value: String,
    @SerialName("count") val count: Float
)

@Serializable
data class MemeTag(
    @SerialName("name") val name: String,
    @SerialName("category") val category: String? = null,
    @SerialName("score") val score: Float? = null,
    @SerialName("source") val source: String? = null
)

@Serializable
data class SearchHistoryTag(
    @SerialName("category") val category: String,
    @SerialName("value") val value: String
)

@Serializable
data class TrendEntry(
    @SerialName("label") val label: String,
    @SerialName("name") val name: String,
    @SerialName("value") val value: Any
)

@Serializable
data class TrendHistoryEntry(
    @SerialName("runId") val runId: String,
    @SerialName("date") val date: String,
    @SerialName("label") val label: String,
    @SerialName("name") val name: String,
    @SerialName("value") val value: Any
)

@Serializable
data class TrendsRun(
    @SerialName("runId") val runId: String,
    @SerialName("createdAt") val createdAt: String,
    @SerialName("status") val status: String
)

@Serializable
data class Facet(
    @SerialName("name") val name: String,
    @SerialName("buckets") val buckets: List<FacetBucket>
)

@Serializable
data class Meme(
    @SerialName("id") val id: String,
    @SerialName("imageUrl") val imageUrl: String,
    @SerialName("originalFileName") val originalFileName: String? = null,
    @SerialName("text") val text: List<String>? = null,
    @SerialName("tags") val tags: List<MemeTag>? = null,
    @SerialName("excluded") val excluded: Boolean? = null
)

@Serializable
data class MemeSearchRequest(
    @SerialName("query") val query: String? = null,
    @SerialName("cursor") val cursor: String? = null,
    @SerialName("limit") val limit: Int? = null,
    @SerialName("tags") val tags: List<MemeTag>? = null
)

@Serializable
data class SearchHistoryItem(
    @SerialName("id") val id: String,
    @SerialName("searchedAt") val searchedAt: String,
    @SerialName("query") val query: String? = null,
    @SerialName("client") val client: String,
    @SerialName("resultCount") val resultCount: Int,
    @SerialName("tags") val tags: List<SearchHistoryTag>
)

@Serializable
data class SearchHistoryResponse(
    @SerialName("items") val items: List<SearchHistoryItem>,
    @SerialName("nextCursor") val nextCursor: String? = null,
    @SerialName("hasNext") val hasNext: Boolean
)

@Serializable
data class MemeSearchResponse(
    @SerialName("items") val items: List<Meme>? = null,
    @SerialName("facets") val facets: List<Facet>? = null,
    @SerialName("nextCursor") val nextCursor: String? = null,
    @SerialName("hasNext") val hasNext: Boolean? = null
)

// Not in shared schemas — internal API utility type
@Serializable
data class HealthResponse(
    @SerialName("status") val status: String
)
