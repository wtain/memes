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
import com.memebrowser.app.ui.excluded.ExcludedScreen
import com.memebrowser.app.ui.search.SearchScreen
import com.memebrowser.app.ui.upload.UploadScreen

@Composable
fun NavGraph() {
    val navController = rememberNavController()
    NavHost(navController = navController, startDestination = "search") {
        composable("search") {
            SearchScreen(
                onMemeClick = { memeId -> navController.navigate("detail/$memeId") },
                onEnvironmentsClick = { navController.navigate("environments") },
                onExcludedClick = { navController.navigate("excluded") },
                onUploadClick = { navController.navigate("upload") },
                onAboutClick = { navController.navigate("about") }
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
        composable("excluded") {
            ExcludedScreen(
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
    }
}