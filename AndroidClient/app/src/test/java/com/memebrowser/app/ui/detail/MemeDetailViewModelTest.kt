package com.memebrowser.app.ui.detail

import app.cash.turbine.test
import androidx.lifecycle.SavedStateHandle
import com.memebrowser.app.data.model.HealthResponse
import com.memebrowser.app.data.repository.MemeRepository
import com.memebrowser.app.util.MainDispatcherRule
import com.memebrowser.app.util.fakeMeme
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.mockk
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class MemeDetailViewModelTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    private lateinit var repo: MemeRepository
    private lateinit var viewModel: MemeDetailViewModel

    private val savedStateHandle = SavedStateHandle(mapOf("memeId" to "meme-1"))

    @Before
    fun setup() {
        repo = mockk(relaxed = true)
        coEvery { repo.getMeme("meme-1") } returns Result.success(fakeMeme)
    }

    @Test
    fun `meme is loaded on init`() = runTest {
        viewModel = MemeDetailViewModel(savedStateHandle, repo)
        viewModel.state.test {
            val state = awaitItem()
            assertNotNull(state.meme)
            assertEquals("meme-1", state.meme!!.id)
            assertFalse(state.isLoading)
        }
    }

    @Test
    fun `getMeme error is surfaced in state`() = runTest {
        coEvery { repo.getMeme("meme-1") } returns Result.failure(Exception("not found"))
        viewModel = MemeDetailViewModel(savedStateHandle, repo)
        viewModel.state.test {
            val state = awaitItem()
            assertEquals("not found", state.error)
            assertNull(state.meme)
        }
    }

    @Test
    fun `toggleExcluded marks an unexcluded meme optimistically`() = runTest {
        viewModel = MemeDetailViewModel(savedStateHandle, repo)
        viewModel.toggleExcluded()
        viewModel.state.test {
            assertTrue(awaitItem().meme!!.excluded!!)
        }
        coVerify(exactly = 1) { repo.markExcluded("meme-1") }
    }

    @Test
    fun `toggleExcluded unmarks an excluded meme optimistically`() = runTest {
        coEvery { repo.getMeme("meme-1") } returns Result.success(fakeMeme.copy(excluded = true))
        viewModel = MemeDetailViewModel(savedStateHandle, repo)
        viewModel.toggleExcluded()
        viewModel.state.test {
            assertFalse(awaitItem().meme!!.excluded!!)
        }
        coVerify(exactly = 1) { repo.unmarkExcluded("meme-1") }
    }

    @Test
    fun `toggleExcluded rolls back on API error`() = runTest {
        coEvery { repo.markExcluded(any()) } returns Result.failure(Exception("server error"))
        viewModel = MemeDetailViewModel(savedStateHandle, repo)
        viewModel.toggleExcluded()
        viewModel.state.test {
            val state = awaitItem()
            // Rolled back: meme.excluded stays false (original value)
            assertFalse(state.meme!!.excluded!!)
            assertEquals("server error", state.error)
        }
    }

    @Test
    fun `dismissError clears the error field`() = runTest {
        coEvery { repo.getMeme("meme-1") } returns Result.failure(Exception("err"))
        viewModel = MemeDetailViewModel(savedStateHandle, repo)
        viewModel.dismissError()
        viewModel.state.test {
            assertNull(awaitItem().error)
        }
    }
}