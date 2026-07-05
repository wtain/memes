package com.memebrowser.app.ui.environment

import app.cash.turbine.test
import com.memebrowser.app.data.api.MemeApiService
import com.memebrowser.app.data.model.BackendEnvironment
import com.memebrowser.app.data.repository.EnvironmentRepository
import com.memebrowser.app.data.repository.EnvironmentWithSelection
import com.memebrowser.app.data.repository.MemeRepository
import com.memebrowser.app.util.MainDispatcherRule
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.test.runTest
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Rule
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class EnvironmentViewModelTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    private val server = MockWebServer()

    private lateinit var envRepo: EnvironmentRepository
    private lateinit var memeRepo: MemeRepository
    private lateinit var viewModel: EnvironmentViewModel

    private lateinit var testEnvs: List<EnvironmentWithSelection>

    @Before
    fun setup() {
        server.start()
        val baseUrl = server.url("/").toString().trimEnd('/')
        testEnvs = listOf(
            EnvironmentWithSelection(BackendEnvironment("test-general", "General", baseUrl, true), true),
            EnvironmentWithSelection(BackendEnvironment("test-it", "IT", baseUrl, true), false),
            EnvironmentWithSelection(BackendEnvironment("test-metal", "Metal", baseUrl, true), false),
        )

        envRepo = mockk(relaxed = true)
        memeRepo = MemeRepository(mockk<MemeApiService>(relaxed = true), OkHttpClient())

        every { envRepo.environmentsWithSelection } returns flowOf(testEnvs)
    }

    @After
    fun teardown() {
        server.shutdown()
    }

    @Test
    fun `environments are exposed from repository flow`() = runTest {
        viewModel = EnvironmentViewModel(envRepo, memeRepo, mockk(relaxed = true), mockk(relaxed = true))
        viewModel.environments.test {
            val envs = awaitItem()
            assertEquals(3, envs.size)
            assertEquals("General", envs[0].environment.name)
        }
    }

    @Test
    fun `selectEnvironment delegates to repository`() = runTest {
        server.enqueue(MockResponse().setResponseCode(200))
        viewModel = EnvironmentViewModel(envRepo, memeRepo, mockk(relaxed = true), mockk(relaxed = true))
        val target = testEnvs[1].environment
        viewModel.selectEnvironment(target)
        coVerify(exactly = 1) { envRepo.selectEnvironment(target) }
    }

    @Test
    fun `addEnvironment delegates to repository`() = runTest {
        viewModel = EnvironmentViewModel(envRepo, memeRepo, mockk(relaxed = true), mockk(relaxed = true))
        viewModel.addEnvironment("Staging", "http://staging.example.com")
        coVerify(exactly = 1) { envRepo.addEnvironment("Staging", "http://staging.example.com") }
    }

    @Test
    fun `updateEnvironment delegates to repository`() = runTest {
        viewModel = EnvironmentViewModel(envRepo, memeRepo, mockk(relaxed = true), mockk(relaxed = true))
        viewModel.updateEnvironment("test-it", "IT Dev", "http://it.example.com")
        coVerify(exactly = 1) { envRepo.updateEnvironment("test-it", "IT Dev", "http://it.example.com") }
    }

    @Test
    fun `deleteEnvironment delegates to repository`() = runTest {
        viewModel = EnvironmentViewModel(envRepo, memeRepo, mockk(relaxed = true), mockk(relaxed = true))
        viewModel.deleteEnvironment("custom-id-123")
        coVerify(exactly = 1) { envRepo.deleteEnvironment("custom-id-123") }
    }

    @Test
    fun `checkHealth sets Online on success`() = runTest {
        server.enqueue(MockResponse().setResponseCode(200))
        viewModel = EnvironmentViewModel(envRepo, memeRepo, mockk(relaxed = true), mockk(relaxed = true))
        viewModel.checkHealth("test-general", testEnvs[0].environment.baseUrl)
        viewModel.uiState.test {
            val state = awaitItem()
            assertEquals(EnvHealthStatus.Online, state.healthMap["test-general"])
        }
    }

    @Test
    fun `checkHealth sets Offline on failure`() = runTest {
        server.enqueue(MockResponse().setResponseCode(500))
        viewModel = EnvironmentViewModel(envRepo, memeRepo, mockk(relaxed = true), mockk(relaxed = true))
        viewModel.checkHealth("test-general", testEnvs[0].environment.baseUrl)
        viewModel.uiState.test {
            val state = awaitItem()
            assertEquals(EnvHealthStatus.Offline, state.healthMap["test-general"])
        }
    }
}