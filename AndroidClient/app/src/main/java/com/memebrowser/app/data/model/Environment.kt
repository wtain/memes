package com.memebrowser.app.data.model

import kotlinx.serialization.Serializable

@Serializable
data class BackendEnvironment(
    val id: String,
    val name: String,
    val baseUrl: String,
    val isBuiltIn: Boolean = false
)

val DEFAULT_ENVIRONMENTS = listOf(
    BackendEnvironment(
        id = "builtin-general",
        name = "General",
        baseUrl = "http://192.168.1.41:8082",
        isBuiltIn = true
    ),
    BackendEnvironment(
        id = "builtin-it",
        name = "IT",
        baseUrl = "http://192.168.1.41:8083",
        isBuiltIn = true
    ),
    BackendEnvironment(
        id = "builtin-metal",
        name = "Metal",
        baseUrl = "http://192.168.1.41:8081",
        isBuiltIn = true
    )
)