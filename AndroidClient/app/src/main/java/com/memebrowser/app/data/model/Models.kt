// GENERATED — do not edit by hand.
// Source: shared/schemas/*.schema.json
// Regenerate: python AndroidClient/scripts/generate_dtos.py

package com.memebrowser.app.data.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement


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
    @SerialName("value") val value: JsonElement
)

@Serializable
data class TrendHistoryEntry(
    @SerialName("runId") val runId: String,
    @SerialName("date") val date: String,
    @SerialName("label") val label: String,
    @SerialName("name") val name: String,
    @SerialName("value") val value: JsonElement
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

@Serializable
data class StatisticsMemeStats(
    @SerialName("total") val total: Int,
    @SerialName("with_embeddings") val withEmbeddings: Int,
    @SerialName("with_ocr") val withOcr: Int,
    @SerialName("with_tags") val withTags: Int,
    @SerialName("with_descriptions") val withDescriptions: Int,
    @SerialName("with_concept_tags") val withConceptTags: Int,
    @SerialName("excluded") val excluded: Int
)

@Serializable
data class StatisticsContentStats(
    @SerialName("ocr_texts") val ocrTexts: Int,
    @SerialName("tags") val tags: Int,
    @SerialName("tag_keys") val tagKeys: Int,
    @SerialName("tag_values") val tagValues: Int,
    @SerialName("concepts") val concepts: Int,
    @SerialName("concept_image_sets") val conceptImageSets: Int,
    @SerialName("concept_images") val conceptImages: Int
)

@Serializable
data class StatisticsTrendsStats(
    @SerialName("runs") val runs: Int,
    @SerialName("feed_sources") val feedSources: Int
)

@Serializable
data class StatisticsResponse(
    @SerialName("memes") val memes: StatisticsMemeStats,
    @SerialName("content") val content: StatisticsContentStats,
    @SerialName("trends") val trends: StatisticsTrendsStats
)

// Not in shared schemas — internal API utility type
@Serializable
data class HealthResponse(
    @SerialName("status") val status: String
)

@Serializable
data class UploadedFile(
    @SerialName("original_filename") val originalFilename: String,
    @SerialName("saved_as") val savedAs: String,
    @SerialName("size_bytes") val sizeBytes: Long,
    @SerialName("content_type") val contentType: String,
    @SerialName("status") val status: String = "ok"
)

@Serializable
data class FailedFile(
    @SerialName("original_filename") val originalFilename: String,
    @SerialName("reason") val reason: String
)

@Serializable
data class UploadResponse(
    @SerialName("uploaded") val uploaded: List<UploadedFile>,
    @SerialName("failed") val failed: List<FailedFile>,
    @SerialName("total_accepted") val totalAccepted: Int,
    @SerialName("total_failed") val totalFailed: Int
)
