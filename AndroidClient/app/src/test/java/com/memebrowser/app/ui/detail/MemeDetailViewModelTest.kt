package com.memebrowser.app.ui.detail

import app.cash.turbine.test
import androidx.lifecycle.SavedStateHandle
import com.memebrowser.app.data.model.DescriptionFeedbackResponse
import com.memebrowser.app.data.model.HealthResponse
import com.memebrowser.app.data.model.ImageDescription
import com.memebrowser.app.data.repository.EnvironmentRepository
import com.memebrowser.app.data.repository.MemeRepository
import com.memebrowser.app.util.MainDispatcherRule
import com.memebrowser.app.util.fakeMeme
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.flow.flowOf
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
    private lateinit var envRepo: EnvironmentRepository
    private lateinit var viewModel: MemeDetailViewModel

    private val savedStateHandle = SavedStateHandle(mapOf("memeId" to "meme-1"))

    @Before
    fun setup() {
        repo = mockk(relaxed = true)
        envRepo = mockk(relaxed = true)
        every { envRepo.selectedEnvironmentName } returns flowOf("TestCollection")
        coEvery { repo.getMeme("meme-1") } returns Result.success(fakeMeme)
        coEvery { repo.getSimilarMemes("meme-1", "image") } returns Result.success(emptyList())
        coEvery { repo.getDescriptions("meme-1") } returns Result.success(emptyList())
    }

    @Test
    fun `meme is loaded on init`() = runTest {
        viewModel = MemeDetailViewModel(savedStateHandle, repo, envRepo)
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
        viewModel = MemeDetailViewModel(savedStateHandle, repo, envRepo)
        viewModel.state.test {
            val state = awaitItem()
            assertEquals("not found", state.error)
            assertNull(state.meme)
        }
    }

    @Test
    fun `toggleFlagged marks an unflagged meme optimistically`() = runTest {
        viewModel = MemeDetailViewModel(savedStateHandle, repo, envRepo)
        viewModel.toggleFlagged()
        viewModel.state.test {
            assertTrue(awaitItem().meme!!.flagged!!)
        }
        coVerify(exactly = 1) { repo.markFlagged("meme-1") }
    }

    @Test
    fun `toggleFlagged unmarks a flagged meme optimistically`() = runTest {
        coEvery { repo.getMeme("meme-1") } returns Result.success(fakeMeme.copy(flagged = true))
        viewModel = MemeDetailViewModel(savedStateHandle, repo, envRepo)
        viewModel.toggleFlagged()
        viewModel.state.test {
            assertFalse(awaitItem().meme!!.flagged!!)
        }
        coVerify(exactly = 1) { repo.unmarkFlagged("meme-1") }
    }

    @Test
    fun `toggleFlagged rolls back on API error`() = runTest {
        coEvery { repo.markFlagged(any()) } returns Result.failure(Exception("server error"))
        viewModel = MemeDetailViewModel(savedStateHandle, repo, envRepo)
        viewModel.toggleFlagged()
        viewModel.state.test {
            val state = awaitItem()
            // Rolled back: meme.flagged stays false (original value)
            assertFalse(state.meme!!.flagged!!)
            assertEquals("server error", state.error)
        }
    }

    @Test
    fun `dismissError clears the error field`() = runTest {
        coEvery { repo.getMeme("meme-1") } returns Result.failure(Exception("err"))
        viewModel = MemeDetailViewModel(savedStateHandle, repo, envRepo)
        viewModel.state.test {
            assertEquals("err", awaitItem().error)
            viewModel.dismissError()
            assertNull(awaitItem().error)
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun `similar memes are populated in state`() = runTest {
        val similar = listOf(fakeMeme.copy(id = "similar-1"))
        coEvery { repo.getSimilarMemes("meme-1", "image") } returns Result.success(similar)
        viewModel = MemeDetailViewModel(savedStateHandle, repo, envRepo)
        viewModel.state.test {
            val state = awaitItem()
            assertEquals(1, state.similarMemes.size)
            assertEquals("similar-1", state.similarMemes[0].id)
            assertFalse(state.isLoadingSimilar)
        }
    }

    @Test
    fun `getSimilarMemes failure is silent and does not set error`() = runTest {
        coEvery { repo.getSimilarMemes("meme-1", "image") } returns Result.failure(Exception("network error"))
        viewModel = MemeDetailViewModel(savedStateHandle, repo, envRepo)
        viewModel.state.test {
            val state = awaitItem()
            assertTrue(state.similarMemes.isEmpty())
            assertFalse(state.isLoadingSimilar)
            assertNull(state.error)
        }
    }

    @Test
    fun `current meme is excluded from similar results`() = runTest {
        // Backend should already filter this out, but we guard on the client side too.
        val withSelf = listOf(
            fakeMeme.copy(id = "meme-1"),   // same ID as current meme
            fakeMeme.copy(id = "similar-1")
        )
        coEvery { repo.getSimilarMemes("meme-1", "image") } returns Result.success(withSelf)
        viewModel = MemeDetailViewModel(savedStateHandle, repo, envRepo)
        viewModel.state.test {
            val state = awaitItem()
            assertEquals(1, state.similarMemes.size)
            assertEquals("similar-1", state.similarMemes[0].id)
        }
    }

    @Test
    fun `duplicate similar IDs are deduplicated`() = runTest {
        // Guards against a LazyRow key collision crash if the API returns the same ID twice.
        val withDuplicates = listOf(
            fakeMeme.copy(id = "similar-1"),
            fakeMeme.copy(id = "similar-1")
        )
        coEvery { repo.getSimilarMemes("meme-1", "image") } returns Result.success(withDuplicates)
        viewModel = MemeDetailViewModel(savedStateHandle, repo, envRepo)
        viewModel.state.test {
            val state = awaitItem()
            assertEquals(1, state.similarMemes.size)
            assertEquals("similar-1", state.similarMemes[0].id)
        }
    }

    @Test
    fun `descriptions are populated in state`() = runTest {
        val descriptions = listOf(
            ImageDescription(promptKey = "general_description", text = "A cat.", modelUsed = "llava", createdAt = "2026-07-18T12:00:00")
        )
        coEvery { repo.getDescriptions("meme-1") } returns Result.success(descriptions)
        viewModel = MemeDetailViewModel(savedStateHandle, repo, envRepo)
        viewModel.state.test {
            val state = awaitItem()
            assertEquals(1, state.descriptions.size)
            assertEquals("general_description", state.descriptions[0].promptKey)
        }
    }

    @Test
    fun `getDescriptions failure is silent and does not set error`() = runTest {
        coEvery { repo.getDescriptions("meme-1") } returns Result.failure(Exception("network error"))
        viewModel = MemeDetailViewModel(savedStateHandle, repo, envRepo)
        viewModel.state.test {
            val state = awaitItem()
            assertTrue(state.descriptions.isEmpty())
            assertNull(state.error)
        }
    }

    @Test
    fun `setSimilarSource refetches with the new source`() = runTest {
        val visual = listOf(fakeMeme.copy(id = "visual-1"))
        val semantic = listOf(fakeMeme.copy(id = "semantic-1"))
        coEvery { repo.getSimilarMemes("meme-1", "image") } returns Result.success(visual)
        coEvery { repo.getSimilarMemes("meme-1", "description") } returns Result.success(semantic)
        viewModel = MemeDetailViewModel(savedStateHandle, repo, envRepo)

        viewModel.setSimilarSource("description")

        viewModel.state.test {
            val state = awaitItem()
            assertEquals("description", state.similarSource)
            assertEquals(1, state.similarMemes.size)
            assertEquals("semantic-1", state.similarMemes[0].id)
        }
        coVerify(exactly = 1) { repo.getSimilarMemes("meme-1", "description") }
    }

    @Test
    fun `initial similarSource is read from the source SavedStateHandle argument`() = runTest {
        val handleWithSource = SavedStateHandle(mapOf("memeId" to "meme-1", "source" to "description"))
        coEvery { repo.getSimilarMemes("meme-1", "description") } returns Result.success(emptyList())
        viewModel = MemeDetailViewModel(handleWithSource, repo, envRepo)

        viewModel.state.test {
            val state = awaitItem()
            assertEquals("description", state.similarSource)
        }
        coVerify(exactly = 1) { repo.getSimilarMemes("meme-1", "description") }
    }

    @Test
    fun `setDescriptionFeedback calls repo and updates matching description in state`() = runTest {
        val descriptions = listOf(
            ImageDescription(promptKey = "general_description", text = "A cat.", modelUsed = "llava", createdAt = "2026-07-19T12:00:00"),
            ImageDescription(promptKey = "humor_explanation", text = "Because cats.", modelUsed = "llava", createdAt = "2026-07-19T12:00:00"),
        )
        coEvery { repo.getDescriptions("meme-1") } returns Result.success(descriptions)
        coEvery { repo.setDescriptionFeedback("meme-1", "general_description", "approve") } returns
            Result.success(DescriptionFeedbackResponse(feedback = "approved"))
        viewModel = MemeDetailViewModel(savedStateHandle, repo, envRepo)

        viewModel.setDescriptionFeedback("general_description", "approve")

        viewModel.state.test {
            val state = awaitItem()
            val updated = state.descriptions.first { it.promptKey == "general_description" }
            val untouched = state.descriptions.first { it.promptKey == "humor_explanation" }
            assertEquals("approved", updated.feedback)
            assertNull(untouched.feedback)
        }
        coVerify(exactly = 1) { repo.setDescriptionFeedback("meme-1", "general_description", "approve") }
    }

    @Test
    fun `setDescriptionFeedback clears feedback when response is null`() = runTest {
        val descriptions = listOf(
            ImageDescription(promptKey = "general_description", text = "A cat.", modelUsed = "llava", createdAt = "2026-07-19T12:00:00", feedback = "approved"),
        )
        coEvery { repo.getDescriptions("meme-1") } returns Result.success(descriptions)
        coEvery { repo.setDescriptionFeedback("meme-1", "general_description", "approve") } returns
            Result.success(DescriptionFeedbackResponse(feedback = null))
        viewModel = MemeDetailViewModel(savedStateHandle, repo, envRepo)

        viewModel.setDescriptionFeedback("general_description", "approve")

        viewModel.state.test {
            val state = awaitItem()
            assertNull(state.descriptions.first { it.promptKey == "general_description" }.feedback)
        }
    }
}
