package com.memebrowser.app.data.sync

import com.memebrowser.app.data.api.MemeApiService
import com.memebrowser.app.data.model.UploadResponse
import com.memebrowser.app.data.repository.RetrofitBatchUploader
import com.memebrowser.app.data.repository.UploadAttempt
import com.memebrowser.app.data.repository.UploadFile
import io.mockk.coEvery
import io.mockk.mockk
import kotlinx.coroutines.test.runTest
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.Protocol
import okhttp3.Request
import okhttp3.ResponseBody.Companion.toResponseBody
import okio.Buffer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import retrofit2.HttpException
import retrofit2.Response
import java.io.IOException
import java.net.SocketTimeoutException

class RetrofitBatchUploaderTest {

    private val files = listOf(UploadFile(TREE, "d1", "a.jpg", "image/jpeg"), UploadFile(TREE, "d2", "b.png", "image/png"))
    private val api = mockk<MemeApiService>()
    private val opened = mutableListOf<String>()
    private val uploader = RetrofitBatchUploader({ _, id -> opened += id; java.io.ByteArrayInputStream("data-$id".toByteArray()) }) { api }

    private fun http(code: Int, body: String, retryAfter: String? = null): HttpException {
        val raw = okhttp3.Response.Builder()
            .request(Request.Builder().url("http://localhost/").build()).protocol(Protocol.HTTP_1_1)
            .code(code).message("m").apply { retryAfter?.let { header("Retry-After", it) } }
            .body(body.toResponseBody("application/json".toMediaType())).build()
        return HttpException(Response.error<UploadResponse>(body.toResponseBody("application/json".toMediaType()), raw))
    }

    @Test
    fun `parts are named files, keep display name and mime, and stream from the source in order`() = runTest {
        var parts: List<MultipartBody.Part> = emptyList()
        coEvery { api.uploadImages(any()) } coAnswers {
            parts = firstArg()
            UploadResponse(emptyList(), emptyList(), 0, 0)
        }
        assertTrue(uploader.upload("e", files) is UploadAttempt.Response)
        assertEquals(2, parts.size)
        val disp = parts.map { it.headers!!["Content-Disposition"]!! }
        assertTrue(disp[0].contains("name=\"files\"") && disp[0].contains("filename=\"a.jpg\""))
        assertTrue(disp[1].contains("filename=\"b.png\""))
        assertEquals("image/png", parts[1].body.contentType().toString())
        assertTrue(opened.isEmpty()) // nothing is read until the request is written
        val sink = Buffer()
        parts[0].body.writeTo(sink)
        assertEquals("data-d1", sink.readUtf8())
        assertEquals(listOf("d1"), opened)
    }

    @Test
    fun `http errors carry code, detail and Retry-After`() = runTest {
        coEvery { api.uploadImages(any()) } throws http(429, """{"detail":"Too many upload requests"}""", "7")
        assertEquals(UploadAttempt.HttpError(429, "Too many upload requests", 7L), uploader.upload("e", files))
        coEvery { api.uploadImages(any()) } throws http(507, "not json")
        assertEquals(UploadAttempt.HttpError(507, "not json", null), uploader.upload("e", files))
    }

    @Test
    fun `io failures are network errors and other exceptions unexpected`() = runTest {
        coEvery { api.uploadImages(any()) } throws SocketTimeoutException("slow")
        assertEquals(UploadAttempt.NetworkError("slow"), uploader.upload("e", files))
        coEvery { api.uploadImages(any()) } throws IOException("reset")
        assertTrue(uploader.upload("e", files) is UploadAttempt.NetworkError)
        coEvery { api.uploadImages(any()) } throws IllegalStateException("bad json")
        assertEquals(UploadAttempt.Unexpected("bad json"), uploader.upload("e", files))
    }

    @Test
    fun `unknown environment is unexpected`() = runTest {
        val u = RetrofitBatchUploader({ _, _ -> error("x") }) { null }
        assertTrue(u.upload("e", files) is UploadAttempt.Unexpected)
    }
}
