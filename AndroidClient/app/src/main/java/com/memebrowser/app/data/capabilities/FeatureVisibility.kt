package com.memebrowser.app.data.capabilities

import com.memebrowser.app.data.model.CapabilitiesResponse

/**
 * What the app shows for the selected backend, derived from `GET /api/diagnostics/capabilities`.
 *
 * Pure: no I/O, no Android. A null [capabilities] (not loaded yet, failed, or an old backend that
 * answers 404) shows everything, which is today's dev behaviour. On `stage == "prod"` the curator
 * and dev-only entries are hidden, and on any stage a feature flag that is false hides its entry.
 */
class FeatureVisibility(private val capabilities: CapabilitiesResponse?) {

    private val prod: Boolean = capabilities?.stage == "prod"
    private val features = capabilities?.features

    private fun shown(flag: Boolean?): Boolean = !prod && flag != false

    val showFlagged: Boolean get() = shown(features?.admin)
    val showFlagToggle: Boolean get() = shown(features?.admin)
    val showDescriptionFeedback: Boolean get() = shown(features?.admin)
    val showHistory: Boolean get() = shown(features?.history)
    val showTrends: Boolean get() = shown(features?.trends)
    val showStatistics: Boolean get() = shown(features?.statistics)
    val showComics: Boolean get() = shown(features?.comics)
    val showAlbumSync: Boolean get() = shown(features?.uploads)

    companion object {
        /** Everything visible: the behaviour before capabilities existed. */
        val ALL = FeatureVisibility(null)
    }
}
