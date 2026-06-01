package com.memebrowser.app.data.api

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class UrlProvider @Inject constructor() {
    private val _baseUrl = MutableStateFlow("http://192.168.1.41:8082/")
    val baseUrl: StateFlow<String> = _baseUrl

    fun setBaseUrl(url: String) {
        _baseUrl.value = url.trimEnd('/') + "/"
    }

    fun current(): String = _baseUrl.value
}