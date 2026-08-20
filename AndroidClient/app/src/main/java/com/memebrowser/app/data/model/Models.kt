// GENERATED — do not edit by hand.
// Source: shared/schemas/*.schema.json
// Regenerate: python AndroidClient/scripts/generate_dtos.py

package com.memebrowser.app.data.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject


@Serializable
data class BatchNamesResponse(
    @SerialName("names") val names: List<String>
)

@Serializable
data class Concept(
    @SerialName("id") val id: Int,
    @SerialName("name") val name: String
)

@Serializable
data class DescriptionFeedbackResponse(
    @SerialName("feedback") val feedback: String? = null
)

@Serializable
data class DuplicateDecisionItem(
    @SerialName("image_id1") val image_id1: String,
    @SerialName("filename1") val filename1: String,
    @SerialName("image_id2") val image_id2: String,
    @SerialName("filename2") val filename2: String,
    @SerialName("decided_at") val decided_at: String
)

@Serializable
data class DuplicatePair(
    @SerialName("image_id1") val image_id1: String,
    @SerialName("image_id2") val image_id2: String
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
data class ImageDescription(
    @SerialName("promptKey") val promptKey: String,
    @SerialName("text") val text: String,
    @SerialName("modelUsed") val modelUsed: String,
    @SerialName("createdAt") val createdAt: String,
    @SerialName("feedback") val feedback: String? = null
)

@Serializable
data class IngestionClusterEdge(
    @SerialName("image_id1") val image_id1: String,
    @SerialName("image_id2") val image_id2: String,
    @SerialName("distance") val distance: Float,
    @SerialName("match_source") val match_source: String?
)

@Serializable
data class IngestionClusterMember(
    @SerialName("image_id") val image_id: String,
    @SerialName("filename") val filename: String,
    @SerialName("status") val status: String,
    @SerialName("ocr_text") val ocr_text: String?
)

@Serializable
data class IngestionDecision(
    @SerialName("image_id") val image_id: String,
    @SerialName("decision") val decision: String
)

@Serializable
data class IngestionFailedDecision(
    @SerialName("image_id") val image_id: String,
    @SerialName("decision") val decision: String,
    @SerialName("error") val error: String
)

@Serializable
data class IngestionMoveFailure(
    @SerialName("image_id") val image_id: String,
    @SerialName("error") val error: String
)

@Serializable
data class IngestionPendingImage(
    @SerialName("image_id") val image_id: String,
    @SerialName("filename") val filename: String,
    @SerialName("created_at") val created_at: String
)

@Serializable
data class IngestionRunStatus(
    @SerialName("run_id") val run_id: String,
    @SerialName("status") val status: String,
    @SerialName("stage") val stage: String?,
    @SerialName("stats") val stats: JsonObject?,
    @SerialName("created_at") val created_at: String,
    @SerialName("completed_at") val completed_at: String?
)

@Serializable
data class IngestionUndoRejectResponse(
    @SerialName("image_id") val image_id: String,
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
data class RunStatusResponse(
    @SerialName("run_id") val run_id: String,
    @SerialName("batch_name") val batch_name: String,
    @SerialName("trigger") val trigger: String,
    @SerialName("status") val status: String,
    @SerialName("created_at") val created_at: String,
    @SerialName("completed_at") val completed_at: String?,
    @SerialName("error") val error: String?
)

@Serializable
data class RunTriggerResponse(
    @SerialName("run_id") val run_id: String,
    @SerialName("status") val status: String
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
    @SerialName("concept_images") val concept_images: Int,
    @SerialName("descriptions_approved") val descriptions_approved: Int,
    @SerialName("descriptions_rejected") val descriptions_rejected: Int,
    @SerialName("descriptions_feedback_total") val descriptions_feedback_total: Int
)

@Serializable
data class StatisticsMemeStats(
    @SerialName("total") val total: Int,
    @SerialName("pending") val pending: Int,
    @SerialName("rejected") val rejected: Int,
    @SerialName("with_embeddings") val with_embeddings: Int,
    @SerialName("with_ocr") val with_ocr: Int,
    @SerialName("with_tags") val with_tags: Int,
    @SerialName("without_tags") val without_tags: Int,
    @SerialName("with_descriptions") val with_descriptions: Int,
    @SerialName("with_concept_tags") val with_concept_tags: Int,
    @SerialName("flagged") val flagged: Int,
    @SerialName("duplicate_clusters") val duplicate_clusters: Int
)

@Serializable
data class StatisticsTrendsStats(
    @SerialName("runs") val runs: Int,
    @SerialName("trend_sources") val trend_sources: Int
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
data class DuplicateDecisionListResponse(
    @SerialName("items") val items: List<DuplicateDecisionItem>,
    @SerialName("total") val total: Int
)

@Serializable
data class DuplicateDismissResponse(
    @SerialName("pairs") val pairs: List<DuplicatePair>
)

@Serializable
data class DuplicateUndoDismissRequest(
    @SerialName("pairs") val pairs: List<DuplicatePair>
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
    @SerialName("flagged") val flagged: Boolean? = null,
    @SerialName("clusterId") val clusterId: Int? = null,
    @SerialName("cosineDistance") val cosineDistance: Float? = null,
    @SerialName("descriptionNote") val descriptionNote: String? = null
)

@Serializable
data class MemeSearchRequest(
    @SerialName("query") val query: String? = null,
    @SerialName("cursor") val cursor: String? = null,
    @SerialName("limit") val limit: Int? = null,
    @SerialName("tags") val tags: List<MemeTag>? = null
)

@Serializable
data class RunListResponse(
    @SerialName("items") val items: List<RunStatusResponse>,
    @SerialName("total") val total: Int
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
data class IngestionCluster(
    @SerialName("members") val members: List<IngestionClusterMember>,
    @SerialName("edges") val edges: List<IngestionClusterEdge>
)

@Serializable
data class IngestionResolveResponse(
    @SerialName("rejected") val rejected: List<String>,
    @SerialName("kept") val kept: List<String>,
    @SerialName("failed") val failed: List<IngestionFailedDecision>,
    @SerialName("move_failed") val move_failed: List<IngestionMoveFailure>
)

@Serializable
data class MemeSearchResponse(
    @SerialName("items") val items: List<Meme>? = null,
    @SerialName("facets") val facets: List<Facet>? = null,
    @SerialName("nextCursor") val nextCursor: String? = null,
    @SerialName("hasNext") val hasNext: Boolean? = null,
    @SerialName("previousCursor") val previousCursor: String? = null
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
