package com.memebrowser.app.data.capabilities

import app.cash.turbine.test
import com.memebrowser.app.data.api.MemeApiService
import com.memebrowser.app.data.api.UrlProvider
import com.memebrowser.app.data.model.CapabilitiesFeatures
import com.memebrowser.app.data.model.CapabilitiesResponse
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.mockk
import kotlinx.coroutines.test.runTest
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import retrofit2.HttpException
import retrofit2.Response
import java.io.IOException

class CapabilitiesRepositoryTest {

    private val prod = CapabilitiesResponse(
        "prod", "general",
        CapabilitiesFeatures(false, true, false, false, false, false, false, false, false)
    )
    private val api = mockk<MemeApiService>()
    private val urls = UrlProvider().apply { setBaseUrl("https://prod.example/") }
    private val repo = CapabilitiesRepository(api, urls)

    private fun notFound() = HttpException(
        Response.error<CapabilitiesResponse>(404, "{}".toResponseBody("application/json".toMediaType()))
    )

    @Test
    fun `emits show-all first then the loaded visibility`() = runTest {
        coEvery { api.getCapabilities() } returns prod
        repo.visibility.test {
            assertTrue(awaitItem().showTrends)
            val loaded = awaitItem()
            assertFalse(loaded.showTrends)
            assertFalse(loaded.showFlagged)
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun `caches per base url for the session`() = runTest {
        coEvery { api.getCapabilities() } returns prod
        repo.visibility.test { awaitItem(); awaitItem(); cancelAndIgnoreRemainingEvents() }
        repo.visibility.test {
            assertFalse(awaitItem().showTrends) // straight from the cache, no show-all flash
            cancelAndIgnoreRemainingEvents()
        }
        coVerify(exactly = 1) { api.getCapabilities() }
    }

    @Test
    fun `404 from an old backend keeps everything visible and is remembered`() = runTest {
        coEvery { api.getCapabilities() } throws notFound()
        repo.visibility.test { assertTrue(awaitItem().showFlagged); cancelAndIgnoreRemainingEvents() }
        repo.visibility.test { assertTrue(awaitItem().showFlagged); cancelAndIgnoreRemainingEvents() }
        coVerify(exactly = 1) { api.getCapabilities() }
    }

    @Test
    fun `network failure keeps everything visible and is retried next time`() = runTest {
        coEvery { api.getCapabilities() } throws IOException("offline")
        repo.visibility.test { assertTrue(awaitItem().showFlagged); cancelAndIgnoreRemainingEvents() }
        coEvery { api.getCapabilities() } returns prod
        repo.visibility.test {
            assertTrue(awaitItem().showFlagged)
            assertFalse(awaitItem().showFlagged)
            cancelAndIgnoreRemainingEvents()
        }
        coVerify(exactly = 2) { api.getCapabilities() }
    }

    @Test
    fun `each backend is read separately`() = runTest {
        coEvery { api.getCapabilities() } returns prod
        repo.visibility.test {
            awaitItem()
            assertFalse(awaitItem().showFlagged)
            coEvery { api.getCapabilities() } returns prod.copy(
                stage = "dev", features = prod.features.copy(trends = true, admin = true)
            )
            urls.setBaseUrl("http://192.168.1.41:8082/")
            assertTrue(awaitItem().showFlagged) // show-all while loading
            assertTrue(awaitItem().showFlagged) // loaded dev answer
            cancelAndIgnoreRemainingEvents()
        }
        coVerify(exactly = 2) { api.getCapabilities() }
    }
}
