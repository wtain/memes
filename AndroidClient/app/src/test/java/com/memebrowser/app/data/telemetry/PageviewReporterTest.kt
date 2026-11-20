package com.memebrowser.app.data.telemetry

import com.memebrowser.app.data.model.PageViewBeacon
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.mockk
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import retrofit2.Response
import java.io.IOException

@OptIn(ExperimentalCoroutinesApi::class)
class PageviewReporterTest {
    private val api = mockk<TelemetryApi>()
    private val scope = TestScope(UnconfinedTestDispatcher())
    private val reporter = PageviewReporter(api, scope)

    @Test
    fun `sends android client and page`() {
        coEvery { api.pageview(any()) } returns Response.success(Unit)
        reporter.report("search")
        coVerify(exactly = 1) { api.pageview(PageViewBeacon("android", "search")) }
    }

    @Test
    fun `strips route arguments`() {
        coEvery { api.pageview(any()) } returns Response.success(Unit)
        reporter.report("detail/abc-123?source=image&searchId=9")
        reporter.report("recommendations?q=cat")
        coVerify { api.pageview(PageViewBeacon("android", "detail")) }
        coVerify { api.pageview(PageViewBeacon("android", "recommendations")) }
    }

    @Test
    fun `unknown destination is not sent`() {
        reporter.report("secret")
        coVerify(exactly = 0) { api.pageview(any()) }
    }

    @Test
    fun `404 disables further sends`() {
        coEvery { api.pageview(any()) } returns
            Response.error(404, "".toResponseBody("text/plain".toMediaType()))
        reporter.report("search")
        reporter.report("about")
        coVerify(exactly = 1) { api.pageview(any()) }
    }

    @Test
    fun `exceptions are swallowed and sending continues`() {
        coEvery { api.pageview(any()) } throws IOException("down")
        reporter.report("search")
        reporter.report("about")
        coVerify(exactly = 2) { api.pageview(any()) }
    }

    @Test
    fun `nav routes are a subset of the backend allowlist`() {
        val stripped = PageviewDestinations.NAV_ROUTES.map(PageviewDestinations::strip).toSet()
        assertTrue(PageviewDestinations.ALLOWED.containsAll(stripped))
        assertEquals(PageviewDestinations.ALLOWED, stripped)
    }
}
