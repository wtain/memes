package com.memebrowser.app.ui

import androidx.compose.runtime.Composable
import androidx.navigation.NavType
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import androidx.navigation.navArgument
import com.memebrowser.app.ui.detail.MemeDetailScreen
import com.memebrowser.app.ui.environment.EnvironmentManagerScreen
import com.memebrowser.app.ui.search.SearchScreen

@Composable
fun NavGraph() {
    val navController = rememberNavController()
    NavHost(navController = navController, startDestination = "search") {
        composable("search") {
            SearchScreen(
                onMemeClick = { memeId -> navController.navigate("detail/$memeId") },
                onEnvironmentsClick = { navController.navigate("environments") }
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
                onNavigateToMeme = { navController.navigate("detail/$it") }
            )
        }
        composable("environments") {
            EnvironmentManagerScreen(
                onBack = { navController.popBackStack() }
            )
        }
    }
}