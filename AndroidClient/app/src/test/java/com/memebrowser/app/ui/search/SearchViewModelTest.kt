package com.memebrowser.app.ui.search

import app.cash.turbine.test
import com.memebrowser.app.data.model.HealthResponse
import com.memebrowser.app.data.repository.EnvironmentRepository
import com.memebrowser.app.data.repository.MemeRepository
import com.memebrowser.app.util.MainDispatcherRule
import com.memebrowser.app.util.fakeSearchEmpty
import com.memebrowser.app.util.fakeSearchPage1
import com.memebrowser.app.util.fakeSearchPage2
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class SearchViewModelTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    private lateinit var repo: MemeRepository
    private lateinit var envRepo: EnvironmentRepository
    private val selectedEnvId = MutableStateFlow("env-1")
    private lateinit var viewModel: SearchViewModel

    @Before
    fun setup() {
        repo = mockk(relaxed = true)
        envRepo = mockk(relaxed = true)
        every { envRepo.selectedEnvironmentId } returns selectedEnvId
        coEvery { repo.search(any(), any(), any(), any()) } returns Result.success(fakeSearchPage1)
        coEvery { repo.health() } returns Result.success(HealthResponse("healthy"))
    }

    @Test
    fun `initial load populates items from search`() = runTest {
        viewModel = SearchViewModel(repo, envRepo)
        viewModel.state.test {
            val state = awaitItem()
            assertEquals(2, state.items.size)
            assertFalse(state.isLoading)
            assertNull(state.error)
        }
    }

    @Test
    fun `health check on init sets Online status`() = runTest {
        viewModel = SearchViewModel(repo, envRepo)
        viewModel.state.test {
            assertEquals(HealthStatus.Online, awaitItem().healthStatus)
        }
    }

    @Test
    fun `health check failure sets Offline status`() = runTest {
        coEvery { repo.health() } returns Result.failure(Exception("unreachable"))
        viewModel = SearchViewModel(repo, envRepo)
        viewModel.state.test {
            assertEquals(HealthStatus.Offline, awaitItem().healthStatus)
        }
    }

    @Test
    fun `search error propagates to state`() = runTest {
        coEvery { repo.search(any(), any(), any(), any()) } returns Result.failure(Exception("timeout"))
        viewModel = SearchViewModel(repo, envRepo)
        viewModel.state.test {
            val state = awaitItem()
            assertEquals("timeout", state.error)
            assertTrue(state.items.isEmpty())
        }
    }

    @Test
    fun `loadMore appends next page and clears cursor when done`() = runTest {
        coEvery { repo.search(null, null, null, any()) } returns Result.success(fakeSearchPage1)
        coEvery { repo.search(null, null, "cursor-abc", any()) } returns Result.success(fakeSearchPage2)
        viewModel = SearchViewModel(repo, envRepo)

        viewModel.loadMore()

        viewModel.state.test {
            val state = awaitItem()
            assertEquals(3, state.items.size)
            assertFalse(state.hasNext)
            assertNull(state.nextCursor)
        }
    }

    @Test
    fun `loadMore is no-op when hasNext is false`() = runTest {
        coEvery { repo.search(any(), any(), any(), any()) } returns Result.success(fakeSearchEmpty)
        viewModel = SearchViewModel(repo, envRepo)

        viewModel.loadMore()

        coVerify(exactly = 1) { repo.search(any(), any(), any(), any()) }
    }

    @Test
    fun `facet toggle adds facet and re-searches`() = runTest {
        viewModel = SearchViewModel(repo, envRepo)
        viewModel.onFacetToggle("tag", "dev")

        viewModel.state.test {
            val state = awaitItem()
            assertEquals(1, state.activeFacets.size)
            assertEquals("tag", state.activeFacets[0].category)
            assertEquals("dev", state.activeFacets[0].value)
        }
        coVerify(atLeast = 2) { repo.search(any(), any(), any(), any()) }
    }

    @Test
    fun `facet toggle removes already-active facet`() = runTest {
        viewModel = SearchViewModel(repo, envRepo)
        viewModel.onFacetToggle("tag", "dev")
        viewModel.onFacetToggle("tag", "dev")

        viewModel.state.test {
            assertTrue(awaitItem().activeFacets.isEmpty())
        }
    }

    @Test
    fun `query change is debounced — rapid changes trigger only one search`() = runTest(
        mainDispatcherRule.dispatcher
    ) {
        viewModel = SearchViewModel(repo, envRepo)
        // Drain the initial search triggered by init
        advanceTimeBy(500)

        viewModel.onQueryChange("a")
        viewModel.onQueryChange("ab")
        viewModel.onQueryChange("abc")
        advanceTimeBy(500) // past the 400 ms debounce

        // 1 initial + 1 debounced (not 1 per keystroke)
        coVerify(exactly = 2) { repo.search(any(), any(), any(), any()) }
    }

    @Test
    fun `dismissError clears error field`() = runTest {
        coEvery { repo.search(any(), any(), any(), any()) } returns Result.failure(Exception("err"))
        viewModel = SearchViewModel(repo, envRepo)
        viewModel.dismissError()

        viewModel.state.test {
            assertNull(awaitItem().error)
        }
    }

    // --- per-environment query tests ---

    @Test
    fun `switching environment triggers immediate search`() = runTest {
        viewModel = SearchViewModel(repo, envRepo)
        advanceTimeBy(500)

        selectedEnvId.value = "env-2"
        advanceTimeBy(100)

        coVerify(atLeast = 2) { repo.search(any(), any(), any(), any()) }
    }

    @Test
    fun `switching to new environment resets query to empty`() = runTest {
        viewModel = SearchViewModel(repo, envRepo)
        advanceTimeBy(500)

        viewModel.onQueryChange("cats")
        advanceTimeBy(500)

        selectedEnvId.value = "env-2"
        advanceTimeBy(100)

        viewModel.state.test {
            assertEquals("", awaitItem().query)
        }
    }

    @Test
    fun `switching back to previous environment restores saved query`() = runTest {
        viewModel = SearchViewModel(repo, envRepo)
        advanceTimeBy(500)

        // Type "cats" while on env-1
        viewModel.onQueryChange("cats")
        advanceTimeBy(500)

        // Switch to env-2, type "dogs"
        selectedEnvId.value = "env-2"
        advanceTimeBy(100)
        viewModel.onQueryChange("dogs")
        advanceTimeBy(500)

        // Switch back to env-1 — "cats" must be restored
        selectedEnvId.value = "env-1"
        advanceTimeBy(100)

        viewModel.state.test {
            assertEquals("cats", awaitItem().query)
        }
    }
}