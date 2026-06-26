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
data class FailedFile(
    @SerialName("original_filename") val original_filename: String,
    @SerialName("reason") val reason: String
)

@Serializable
data class HealthResponse(
    @SerialName("status") val status: String
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
data class StatisticsContentStats(
    @SerialName("ocr_texts") val ocr_texts: Int,
    @SerialName("tags") val tags: Int,
    @SerialName("tag_keys") val tag_keys: Int,
    @SerialName("tag_values") val tag_values: Int,
    @SerialName("concepts") val concepts: Int,
    @SerialName("concept_image_sets") val concept_image_sets: Int,
    @SerialName("concept_images") val concept_images: Int
)

@Serializable
data class StatisticsMemeStats(
    @SerialName("total") val total: Int,
    @SerialName("with_embeddings") val with_embeddings: Int,
    @SerialName("with_ocr") val with_ocr: Int,
    @SerialName("with_tags") val with_tags: Int,
    @SerialName("without_tags") val without_tags: Int,
    @SerialName("with_descriptions") val with_descriptions: Int,
    @SerialName("with_concept_tags") val with_concept_tags: Int,
    @SerialName("excluded") val excluded: Int
)

@Serializable
data class StatisticsTrendsStats(
    @SerialName("runs") val runs: Int,
    @SerialName("feed_sources") val feed_sources: Int
)

@Serializable
data class TrendEntry(
    @SerialName("label") val label: String,
    @SerialName("name") val name: String,
    @SerialName("value") val value: Int
)

@Serializable
data class TrendHistoryEntry(
    @SerialName("runId") val runId: String,
    @SerialName("date") val date: String,
    @SerialName("label") val label: String,
    @SerialName("name") val name: String,
    @SerialName("value") val value: Int
)

@Serializable
data class TrendsRun(
    @SerialName("runId") val runId: String,
    @SerialName("createdAt") val createdAt: String,
    @SerialName("status") val status: String
)

@Serializable
data class UploadedFile(
    @SerialName("original_filename") val original_filename: String,
    @SerialName("saved_as") val saved_as: String,
    @SerialName("size_bytes") val size_bytes: Int,
    @SerialName("content_type") val content_type: String,
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
    @SerialName("excluded") val excluded: Boolean? = null,
    @SerialName("clusterId") val clusterId: Int? = null,
    @SerialName("cosineDistance") val cosineDistance: Float? = null
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

@Serializable
data class UploadResponse(
    @SerialName("uploaded") val uploaded: List<UploadedFile>,
    @SerialName("failed") val failed: List<FailedFile>,
    @SerialName("total_accepted") val total_accepted: Int,
    @SerialName("total_failed") val total_failed: Int
)

@Serializable
data class StatisticsResponse(
    @SerialName("memes") val memes: StatisticsMemeStats,
    @SerialName("content") val content: StatisticsContentStats,
    @SerialName("trends") val trends: StatisticsTrendsStats
)
