package com.memebrowser.app.ui

import androidx.compose.runtime.Composable
import androidx.navigation.NavType
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import androidx.navigation.navArgument
import com.memebrowser.app.ui.about.AboutScreen
import com.memebrowser.app.ui.detail.MemeDetailScreen
import com.memebrowser.app.ui.environment.EnvironmentManagerScreen
import com.memebrowser.app.ui.flagged.FlaggedScreen
import com.memebrowser.app.ui.recommendations.RecommendationsScreen
import com.memebrowser.app.ui.search.SearchScreen
import com.memebrowser.app.ui.statistics.StatisticsScreen
import com.memebrowser.app.ui.trends.TrendsScreen
import com.memebrowser.app.ui.upload.UploadScreen
import java.net.URLEncoder

@Composable
fun NavGraph() {
    val navController = rememberNavController()
    NavHost(navController = navController, startDestination = "search") {
        composable("search") {
            SearchScreen(
                onMemeClick = { memeId -> navController.navigate("detail/$memeId") },
                onEnvironmentsClick = { navController.navigate("environments") },
                onFlaggedClick = { navController.navigate("flagged") },
                onUploadClick = { navController.navigate("upload") },
                onAboutClick = { navController.navigate("about") },
                onTrendsClick = { navController.navigate("trends") },
                onRecommendationsClick = { navController.navigate("recommendations") },
                onStatisticsClick = { navController.navigate("statistics") }
            )
        }
        composable(
            route = "detail/{memeId}",
            arguments = listOf(navArgument("memeId") { type = NavType.StringType })
        ) { backStack ->
            val memeId = backStack.arguments!!.getString("memeId")!!
            MemeDetailScreen(
                memeId = memeId,
                onBack = { navController.popBackStack() },
                onNavigateToMeme = { id ->
                    navController.navigate("detail/$id") {
                        popUpTo("detail/{memeId}") { inclusive = true }
                    }
                },
                onTagClick = { category, value ->
                    navController.getBackStackEntry("search")
                        .savedStateHandle["pending_tag"] = "$category:$value"
                    navController.popBackStack()
                }
            )
        }
        composable("flagged") {
            FlaggedScreen(
                onBack = { navController.popBackStack() },
                onMemeClick = { memeId -> navController.navigate("detail/$memeId") }
            )
        }
        composable("environments") {
            EnvironmentManagerScreen(
                onBack = { navController.popBackStack() }
            )
        }
        composable("upload") {
            UploadScreen(onBack = { navController.popBackStack() })
        }
        composable("about") {
            AboutScreen(onBack = { navController.popBackStack() })
        }
        composable("trends") {
            TrendsScreen(
                onBack = { navController.popBackStack() },
                onNavigateToRecommendations = { query ->
                    val encoded = URLEncoder.encode(query, "UTF-8")
                    navController.navigate("recommendations?q=$encoded")
                }
            )
        }
        composable("statistics") {
            StatisticsScreen(onBack = { navController.popBackStack() })
        }
        composable(
            route = "recommendations?q={query}",
            arguments = listOf(navArgument("query") {
                type = NavType.StringType
                nullable = true
                defaultValue = null
            })
        ) {
            RecommendationsScreen(
                onBack = { navController.popBackStack() },
                onMemeClick = { memeId -> navController.navigate("detail/$memeId") }
            )
        }
    }
}