package com.memebrowser.app.service

/** Pure decisions of [SyncService] (no Android types), unit tested. */
object SyncNotificationPlan {
    /** The id of the service's placeholder notification (a start with nothing to show); never an environment's id. */
    const val PLACEHOLDER_ID = 1

    /**
     * Stable notification id of an environment: the same id for every run of that environment in every process, so a
     * new run replaces its previous terminal notification. Always >= 2 (0 is invalid for `startForeground`, 1 is the
     * placeholder).
     */
    fun notificationId(environmentId: String): Int = (environmentId.hashCode() and 0x3FFFFFFF) + 2

    /**
     * Which environment's notification is the service's foreground one. [running] is in start order (oldest first).
     * The current one stays while it runs (no needless re-promotion); otherwise the oldest running one; null when none runs.
     */
    fun foreground(running: List<String>, current: String?): String? =
        if (current != null && current in running) current else running.firstOrNull()

    /**
     * The start order after the engine reported [active]: environments already known keep their place, newly active
     * ones are appended (sorted, so a set that arrives with several new ones gives a deterministic order).
     */
    fun order(known: List<String>, active: Set<String>): List<String> =
        known + (active - known.toSet()).sorted()
}
