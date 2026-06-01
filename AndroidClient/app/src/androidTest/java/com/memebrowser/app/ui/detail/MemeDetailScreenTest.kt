package com.memebrowser.app.ui.detail

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.performClick
import androidx.lifecycle.SavedStateHandle
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.memebrowser.app.data.repository.MemeRepository
import com.memebrowser.app.ui.theme.MemeBrowserTheme
import com.memebrowser.app.util.androidFakeMeme
import io.mockk.coEvery
import io.mockk.mockk
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
    private lateinit var viewModel: MemeDetailViewModel

    @Before
    fun setup() {
        repo = mockk(relaxed = true)
        coEvery { repo.getMeme("meme-1") } returns Result.success(androidFakeMeme)
        viewModel = MemeDetailViewModel(
            savedStateHandle = SavedStateHandle(mapOf("memeId" to "meme-1")),
            repo = repo
        )
    }

    private fun setContent(onBack: () -> Unit = {}) {
        composeTestRule.setContent {
            MemeBrowserTheme {
                MemeDetailScreen(
                    memeId = "meme-1",
                    onBack = onBack,
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
    fun excludeButton_showsMarkExcluded_whenNotExcluded() {
        setContent()
        composeTestRule.onNodeWithContentDescription("Mark excluded").assertIsDisplayed()
    }

    @Test
    fun excludeButton_showsUnmarkExcluded_whenExcluded() {
        coEvery { repo.getMeme("meme-1") } returns Result.success(androidFakeMeme.copy(excluded = true))
        viewModel = MemeDetailViewModel(SavedStateHandle(mapOf("memeId" to "meme-1")), repo)
        setContent()
        composeTestRule.onNodeWithContentDescription("Unmark excluded").assertIsDisplayed()
    }
}