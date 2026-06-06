package com.memebrowser.app.ui.search

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTextInput
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.memebrowser.app.data.repository.MemeRepository
import com.memebrowser.app.ui.theme.MemeBrowserTheme
import com.memebrowser.app.util.androidFakeHealthy
import com.memebrowser.app.util.androidFakeSearchResponse
import io.mockk.coEvery
import io.mockk.mockk
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class SearchScreenTest {

    @get:Rule
    val composeTestRule = createComposeRule()

    private lateinit var repo: MemeRepository
    private lateinit var viewModel: SearchViewModel

    @Before
    fun setup() {
        repo = mockk(relaxed = true)
        coEvery { repo.search(any(), any(), any(), any()) } returns Result.success(androidFakeSearchResponse)
        coEvery { repo.health() } returns Result.success(androidFakeHealthy)
        viewModel = SearchViewModel(repo)
    }

    private fun setContent(
        onMemeClick: (String) -> Unit = {},
        onEnvironmentsClick: () -> Unit = {}
    ) {
        composeTestRule.setContent {
            MemeBrowserTheme {
                SearchScreen(
                    onMemeClick = onMemeClick,
                    onEnvironmentsClick = onEnvironmentsClick,
                    onExcludedClick = {},
                    viewModel = viewModel
                )
            }
        }
        composeTestRule.waitForIdle()
    }

    @Test
    fun searchBar_isDisplayed() {
        setContent()
        composeTestRule.onNodeWithText("Search memes…").assertIsDisplayed()
    }

    @Test
    fun settingsIcon_isDisplayed() {
        setContent()
        composeTestRule.onNodeWithContentDescription("Environments").assertIsDisplayed()
    }

    @Test
    fun settingsIcon_click_invokesCallback() {
        var clicked = false
        setContent(onEnvironmentsClick = { clicked = true })
        composeTestRule.onNodeWithContentDescription("Environments").performClick()
        assertTrue(clicked)
    }

    @Test
    fun memeGrid_isDisplayedAfterLoad() {
        setContent()
        composeTestRule.waitUntil(timeoutMillis = 5_000) {
            composeTestRule.onAllNodes(androidx.compose.ui.test.hasTestTag("meme_grid"))
                .fetchSemanticsNodes().isNotEmpty()
        }
        composeTestRule.onNodeWithTag("meme_grid").assertIsDisplayed()
    }

    @Test
    fun excludedMeme_showsExcludedBadge() {
        setContent()
        composeTestRule.waitUntil(timeoutMillis = 5_000) {
            composeTestRule.onAllNodes(androidx.compose.ui.test.hasText("excluded"))
                .fetchSemanticsNodes().isNotEmpty()
        }
        composeTestRule.onNodeWithText("excluded").assertIsDisplayed()
    }

    @Test
    fun memeCell_click_invokesCallback() {
        var clickedId = ""
        setContent(onMemeClick = { clickedId = it })
        composeTestRule.waitUntil(timeoutMillis = 5_000) {
            composeTestRule.onAllNodes(androidx.compose.ui.test.hasTestTag("meme_cell_meme-1"))
                .fetchSemanticsNodes().isNotEmpty()
        }
        composeTestRule.onNodeWithTag("meme_cell_meme-1").performClick()
        assertTrue(clickedId == "meme-1")
    }

    @Test
    fun searchInput_updatesQuery() {
        setContent()
        composeTestRule.onNodeWithText("Search memes…").performTextInput("cats")
        composeTestRule.waitForIdle()
        composeTestRule.onNodeWithText("cats").assertIsDisplayed()
    }
}