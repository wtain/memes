package com.memebrowser.app.data.repository

import com.memebrowser.app.data.api.MemeApiService
import com.memebrowser.app.data.model.UploadResponse
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.MultipartBody
import okhttp3.RequestBody
import okio.Buffer
import okio.BufferedSink
import okio.ForwardingSource
import okio.source
import retrofit2.HttpException
import java.io.IOException
import java.io.InputStream
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter
import java.time.format.DateTimeParseException
import java.util.concurrent.atomic.AtomicReference
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

    /** Reading a local file failed with a SecurityException while the request was written (grant lost or denied). */
    data class AccessDenied(val message: String?) : UploadAttempt

    /** Reading a local file failed (not a network problem) while the request was written. */
    data class ReadFailed(val message: String?) : UploadAttempt

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

private const val MAX_RETRY_AFTER_PARSE_SECONDS = Long.MAX_VALUE / 2000

/**
 * Parses a `Retry-After` header: delta-seconds, or an HTTP-date (RFC 1123) relative to [nowMs]. Returns whole seconds
 * (never negative; a date in the past is 0), or null for blank/garbage. The caller applies its own cap.
 */
fun parseRetryAfter(value: String?, nowMs: Long): Long? {
    val v = value?.trim().orEmpty()
    if (v.isEmpty()) return null
    if (v.all { it in '0'..'9' }) return v.toLongOrNull()?.coerceAtMost(MAX_RETRY_AFTER_PARSE_SECONDS) ?: MAX_RETRY_AFTER_PARSE_SECONDS
    return try {
        val at = ZonedDateTime.parse(v, DateTimeFormatter.RFC_1123_DATE_TIME).toInstant().toEpochMilli()
        val deltaMs = at - nowMs
        if (deltaMs <= 0) 0 else (deltaMs + 999) / 1000
    } catch (_: DateTimeParseException) {
        null
    }
}

/**
 * Real [BatchUploader]. [apiFor] returns the service for an environment (a client with long timeouts and the
 * environment's own base URL), or null for an unknown environment. Files are streamed from [source] while the
 * request is written, never held in memory; the part length is unknown, so the request is chunked.
 */
class RetrofitBatchUploader(
    private val source: AlbumFileSource,
    private val clock: () -> Long = System::currentTimeMillis,
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
        // The first failure while reading a LOCAL file; OkHttp wraps it in an IOException, which would look like a network error.
        val readFailure = AtomicReference<Throwable?>(null)
        return try {
            val parts = files.map { part(it, readFailure) }
            UploadAttempt.Response(api.uploadImages(parts))
        } catch (e: CancellationException) {
            throw e
        } catch (e: HttpException) {
            UploadAttempt.HttpError(e.code(), detailOf(e), parseRetryAfter(e.response()?.headers()?.get("Retry-After"), clock()))
        } catch (e: Exception) {
            val local = readFailure.get()
            when {
                local is SecurityException -> UploadAttempt.AccessDenied(local.message)
                local != null -> UploadAttempt.ReadFailed(local.message ?: local.javaClass.simpleName)
                e is IOException -> UploadAttempt.NetworkError(e.message)
                else -> UploadAttempt.Unexpected(e.message)
            }
        }
    }

    private fun part(file: UploadFile, readFailure: AtomicReference<Throwable?>): MultipartBody.Part {
        // A malformed stored mime type must not throw here (it would wedge the batch forever): send it as opaque bytes.
        val type = file.mimeType.toMediaTypeOrNull() ?: "application/octet-stream".toMediaType()
        val body = object : RequestBody() {
            override fun contentType() = type
            override fun writeTo(sink: BufferedSink) {
                val input = try {
                    source.open(file.treeUri, file.documentId)
                } catch (t: Throwable) {
                    readFailure.compareAndSet(null, t)
                    throw t
                }
                input.use {
                    val watched = object : ForwardingSource(it.source()) {
                        override fun read(sink: Buffer, byteCount: Long): Long = try {
                            super.read(sink, byteCount)
                        } catch (t: Throwable) {
                            readFailure.compareAndSet(null, t)
                            throw t
                        }
                    }
                    sink.writeAll(watched)
                }
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
