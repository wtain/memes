package com.memebrowser.app.ui

import androidx.lifecycle.ViewModel
import com.memebrowser.app.data.telemetry.PageviewReporter
import dagger.hilt.android.lifecycle.HiltViewModel
import javax.inject.Inject

/** Hands the singleton reporter to NavGraph. */
@HiltViewModel
class PageviewViewModel @Inject constructor(val reporter: PageviewReporter) : ViewModel()
