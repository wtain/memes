package com.memebrowser.app.data.repository

import com.memebrowser.app.data.api.MemeApiService
import com.memebrowser.app.data.model.UploadResponse
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.RequestBody
import okio.BufferedSink
import okio.source
import retrofit2.HttpException
import java.io.IOException
import java.io.InputStream
import kotlin.coroutines.cancellation.CancellationException

/** A tracked album file to send: where to open it (tree + document id), the part name and the part content type. */
data class UploadFile(
    val treeUri: String,
    val documentId: String,
    val displayName: String,
    val mimeType: String,
)

/** Opens an album document for reading. Throws (FileNotFoundException, SecurityException, ...) when it cannot. */
fun interface AlbumFileSource {
    fun open(treeUri: String, documentId: String): InputStream
}

/** What one `POST /api/uploads` call came to. Never thrown: failures are values, except cancellation. */
sealed interface UploadAttempt {
    /** HTTP 200 with a parsed body. */
    data class Response(val body: UploadResponse) : UploadAttempt

    /** A non-2xx answer. [detail] is the backend's `detail` text when it had one; [retryAfterSeconds] the header if sent. */
    data class HttpError(val code: Int, val detail: String?, val retryAfterSeconds: Long?) : UploadAttempt

    /** No answer: connect/read/write failure, timeout, broken connection. */
    data class NetworkError(val message: String?) : UploadAttempt

    /** Anything else: unparsable 200 body, invalid environment URL, ... */
    data class Unexpected(val message: String?) : UploadAttempt
}

/**
 * The seam between the sync engine and Retrofit. One call sends [files] as one multipart request, in list order,
 * so the backend's per-file `index` is the position in [files]. It goes to the backend of [environmentId], NOT the
 * currently selected one (the user may switch environments while a sync runs).
 */
interface BatchUploader {
    suspend fun upload(environmentId: String, files: List<UploadFile>): UploadAttempt
}

/**
 * Real [BatchUploader]. [apiFor] returns the service for an environment (a client with long timeouts and the
 * environment's own base URL), or null for an unknown environment. Files are streamed from [source] while the
 * request is written, never held in memory; the part length is unknown, so the request is chunked.
 */
class RetrofitBatchUploader(
    private val source: AlbumFileSource,
    private val apiFor: suspend (environmentId: String) -> MemeApiService?,
) : BatchUploader {

    override suspend fun upload(environmentId: String, files: List<UploadFile>): UploadAttempt {
        val api = try {
            apiFor(environmentId)
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            return UploadAttempt.Unexpected(e.message ?: "invalid environment URL")
        } ?: return UploadAttempt.Unexpected("unknown environment")
        val parts = files.map { part(it) }
        return try {
            UploadAttempt.Response(api.uploadImages(parts))
        } catch (e: CancellationException) {
            throw e
        } catch (e: HttpException) {
            UploadAttempt.HttpError(e.code(), detailOf(e), e.response()?.headers()?.get("Retry-After")?.trim()?.toLongOrNull())
        } catch (e: IOException) {
            UploadAttempt.NetworkError(e.message)
        } catch (e: Exception) {
            UploadAttempt.Unexpected(e.message)
        }
    }

    private fun part(file: UploadFile): MultipartBody.Part {
        val body = object : RequestBody() {
            override fun contentType() = file.mimeType.toMediaType()
            override fun writeTo(sink: BufferedSink) {
                source.open(file.treeUri, file.documentId).use { sink.writeAll(it.source()) }
            }
        }
        return MultipartBody.Part.createFormData("files", file.displayName, body)
    }

    private fun detailOf(e: HttpException): String? {
        val text = try {
            e.response()?.errorBody()?.string()
        } catch (_: Exception) {
            null
        }?.takeIf { it.isNotBlank() } ?: return null
        return try {
            ((Json.parseToJsonElement(text) as? JsonObject)?.get("detail") as? JsonPrimitive)?.content
        } catch (_: Exception) {
            null
        } ?: text.take(200)
    }
}
