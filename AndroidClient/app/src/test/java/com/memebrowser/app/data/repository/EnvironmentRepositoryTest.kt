package com.memebrowser.app.data.repository

import com.memebrowser.app.data.api.UrlProvider
import com.memebrowser.app.data.model.BackendEnvironment
import com.memebrowser.app.data.model.DEFAULT_ENVIRONMENTS
import com.memebrowser.app.data.store.EnvironmentStore
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.test.runTest
import org.junit.Before
import org.junit.Test

class EnvironmentRepositoryTest {

    private lateinit var store: EnvironmentStore
    private lateinit var urlProvider: UrlProvider
    private lateinit var repository: EnvironmentRepository

    @Before
    fun setup() {
        store = mockk(relaxed = true)
        urlProvider = mockk(relaxed = true)
        repository = EnvironmentRepository(store, urlProvider)
    }

    @Test
    fun `restoreSelectedBaseUrl points urlProvider at the persisted selection`() = runTest {
        val selected = BackendEnvironment("builtin-it", "IT", "http://192.168.1.41:8083", true)
        every { store.environments } returns flowOf(DEFAULT_ENVIRONMENTS)
        every { store.selectedEnvironmentId } returns flowOf(selected.id)

        repository.restoreSelectedBaseUrl()

        coVerify(exactly = 1) { urlProvider.setBaseUrl(selected.baseUrl) }
    }

    @Test
    fun `restoreSelectedBaseUrl falls back to the first environment when the persisted id is unknown`() = runTest {
        every { store.environments } returns flowOf(DEFAULT_ENVIRONMENTS)
        every { store.selectedEnvironmentId } returns flowOf("no-longer-exists")

        repository.restoreSelectedBaseUrl()

        coVerify(exactly = 1) { urlProvider.setBaseUrl(DEFAULT_ENVIRONMENTS.first().baseUrl) }
    }

    @Test
    fun `restoreSelectedBaseUrl does nothing when there are no environments`() = runTest {
        every { store.environments } returns flowOf(emptyList())
        every { store.selectedEnvironmentId } returns flowOf("anything")

        repository.restoreSelectedBaseUrl()

        coVerify(exactly = 0) { urlProvider.setBaseUrl(any()) }
    }
}
