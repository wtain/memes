package com.memebrowser.app.ui.environment

import app.cash.turbine.test
import com.memebrowser.app.data.model.DEFAULT_ENVIRONMENTS
import com.memebrowser.app.data.repository.EnvironmentRepository
import com.memebrowser.app.data.repository.EnvironmentWithSelection
import com.memebrowser.app.data.repository.MemeRepository
import com.memebrowser.app.util.MainDispatcherRule
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Rule
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class EnvironmentViewModelTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    private lateinit var envRepo: EnvironmentRepository
    private lateinit var memeRepo: MemeRepository
    private lateinit var viewModel: EnvironmentViewModel

    private val defaultEnvs = DEFAULT_ENVIRONMENTS.mapIndexed { i, env ->
        EnvironmentWithSelection(env, i == 0)
    }

    @Before
    fun setup() {
        envRepo = mockk(relaxed = true)
        memeRepo = mockk(relaxed = true)
        every { envRepo.environmentsWithSelection } returns flowOf(defaultEnvs)
    }

    @Test
    fun `environments are exposed from repository flow`() = runTest {
        viewModel = EnvironmentViewModel(envRepo, memeRepo)
        viewModel.environments.test {
            val envs = awaitItem()
            assertEquals(3, envs.size)
            assertEquals("General", envs[0].environment.name)
        }
    }

    @Test
    fun `selectEnvironment delegates to repository`() = runTest {
        viewModel = EnvironmentViewModel(envRepo, memeRepo)
        val target = DEFAULT_ENVIRONMENTS[1]
        viewModel.selectEnvironment(target)
        coVerify(exactly = 1) { envRepo.selectEnvironment(target) }
    }

    @Test
    fun `addEnvironment delegates to repository`() = runTest {
        viewModel = EnvironmentViewModel(envRepo, memeRepo)
        viewModel.addEnvironment("Staging", "http://10.0.0.5:9000")
        coVerify(exactly = 1) { envRepo.addEnvironment("Staging", "http://10.0.0.5:9000") }
    }

    @Test
    fun `updateEnvironment delegates to repository`() = runTest {
        viewModel = EnvironmentViewModel(envRepo, memeRepo)
        viewModel.updateEnvironment("builtin-it", "IT Dev", "http://10.0.0.5:8083")
        coVerify(exactly = 1) { envRepo.updateEnvironment("builtin-it", "IT Dev", "http://10.0.0.5:8083") }
    }

    @Test
    fun `deleteEnvironment delegates to repository`() = runTest {
        viewModel = EnvironmentViewModel(envRepo, memeRepo)
        viewModel.deleteEnvironment("custom-id-123")
        coVerify(exactly = 1) { envRepo.deleteEnvironment("custom-id-123") }
    }

    @Test
    fun `checkHealth sets Checking then Online on success`() = runTest {
        viewModel = EnvironmentViewModel(envRepo, memeRepo)
        viewModel.checkHealth("builtin-general", "http://192.168.1.41:8082")
        viewModel.uiState.test {
            val state = awaitItem()
            assertEquals(EnvHealthStatus.Online, state.healthMap["builtin-general"])
        }
    }

    @Test
    fun `checkHealth sets Offline on failure`() = runTest {
        coEvery { memeRepo.healthCheck(any()) } returns Result.failure(Exception("unreachable"))
        viewModel = EnvironmentViewModel(envRepo, memeRepo)
        viewModel.checkHealth("builtin-general", "http://192.168.1.41:8082")
        viewModel.uiState.test {
            val state = awaitItem()
            assertEquals(EnvHealthStatus.Offline, state.healthMap["builtin-general"])
        }
    }
}