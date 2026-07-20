package com.memebrowser.app.ui.detail

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.performClick
import androidx.lifecycle.SavedStateHandle
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.memebrowser.app.data.repository.EnvironmentRepository
import com.memebrowser.app.data.repository.MemeRepository
import com.memebrowser.app.ui.theme.MemeBrowserTheme
import com.memebrowser.app.util.androidFakeMeme
import io.mockk.coEvery
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.flow.flowOf
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class MemeDetailScreenTest {

    @get:Rule
    val composeTestRule = createComposeRule()

    private lateinit var repo: MemeRepository
    private lateinit var envRepo: EnvironmentRepository
    private lateinit var viewModel: MemeDetailViewModel

    @Before
    fun setup() {
        repo = mockk(relaxed = true)
        envRepo = mockk(relaxed = true)
        every { envRepo.selectedEnvironmentName } returns flowOf("TestCollection")
        coEvery { repo.getMeme("meme-1") } returns Result.success(androidFakeMeme)
        coEvery { repo.getSimilarMemes("meme-1", "image") } returns Result.success(emptyList())
        coEvery { repo.getDescriptions("meme-1") } returns Result.success(emptyList())
        viewModel = MemeDetailViewModel(
            savedStateHandle = SavedStateHandle(mapOf("memeId" to "meme-1")),
            repo = repo,
            envRepo = envRepo
        )
    }

    private fun setContent(
        onBack: () -> Unit = {},
        onNavigateToMeme: (id: String, source: String) -> Unit = { _, _ -> }
    ) {
        composeTestRule.setContent {
            MemeBrowserTheme {
                MemeDetailScreen(
                    memeId = "meme-1",
                    onBack = onBack,
                    onNavigateToMeme = onNavigateToMeme,
                    viewModel = viewModel
                )
            }
        }
        composeTestRule.waitForIdle()
    }

    @Test
    fun backButton_isDisplayed() {
        setContent()
        composeTestRule.onNodeWithContentDescription("Back").assertIsDisplayed()
    }

    @Test
    fun backButton_click_invokesCallback() {
        var backPressed = false
        setContent(onBack = { backPressed = true })
        composeTestRule.onNodeWithContentDescription("Back").performClick()
        assertTrue(backPressed)
    }

    @Test
    fun saveButton_isDisplayed() {
        setContent()
        composeTestRule.onNodeWithContentDescription("Save to gallery").assertIsDisplayed()
    }

    @Test
    fun shareButton_isDisplayed() {
        setContent()
        composeTestRule.onNodeWithContentDescription("Share").assertIsDisplayed()
    }

    @Test
    fun flagButton_showsMarkFlagged_whenNotFlagged() {
        setContent()
        composeTestRule.onNodeWithContentDescription("Mark flagged").assertIsDisplayed()
    }

    @Test
    fun flagButton_showsUnmarkFlagged_whenFlagged() {
        coEvery { repo.getMeme("meme-1") } returns Result.success(androidFakeMeme.copy(flagged = true))
        viewModel = MemeDetailViewModel(SavedStateHandle(mapOf("memeId" to "meme-1")), repo, envRepo)
        setContent()
        composeTestRule.onNodeWithContentDescription("Unmark flagged").assertIsDisplayed()
    }

    @Test
    fun similarThumbnail_click_invokesNavigateCallback() {
        val similarMeme = androidFakeMeme.copy(id = "similar-1", originalFileName = "similar.jpg")
        coEvery { repo.getSimilarMemes("meme-1", "image") } returns Result.success(listOf(similarMeme))
        viewModel = MemeDetailViewModel(SavedStateHandle(mapOf("memeId" to "meme-1")), repo, envRepo)
        var navigatedTo: String? = null
        setContent(onNavigateToMeme = { id, _ -> navigatedTo = id })
        composeTestRule.onNodeWithContentDescription("similar.jpg").performClick()
        assertTrue(navigatedTo == "similar-1")
    }
}