package com.memebrowser.app.service

/** What [SyncService] does with its foreground slot once it knows which environments it follows. */
sealed interface ForegroundChoice {
    /** The current foreground environment is followed: leave it. */
    data object Keep : ForegroundChoice

    /** Promote [env]'s notification (the current one is not followed, or there was none). */
    data class Switch(val env: String) : ForegroundChoice

    /** Nothing is followed: leave the foreground (removing the placeholder) and stop. */
    data object Stop : ForegroundChoice
}

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
     * The foreground decision after a start was reconciled: [tracked] are the followed environments in start order. A
     * [current] that is not followed (its run ended between the start and the reconcile) is never kept, so the service
     * cannot stay foreground with a notification nothing ends.
     */
    fun reconcileForeground(current: String?, tracked: List<String>): ForegroundChoice {
        val next = foreground(tracked, current) ?: return ForegroundChoice.Stop
        return if (next == current) ForegroundChoice.Keep else ForegroundChoice.Switch(next)
    }

    /**
     * Whether a Stop tapped on an environment's notification must also remove it: only when no run of that environment
     * exists and the service does not follow it (a notification stranded by a process that died). A followed run gets its
     * terminal notification from the service instead.
     */
    fun cancelOnStop(running: Boolean, followed: Boolean): Boolean = !running && !followed

    /**
     * The start order after the engine reported [active]: environments already known keep their place, newly active
     * ones are appended (sorted, so a set that arrives with several new ones gives a deterministic order).
     */
    fun order(known: List<String>, active: Set<String>): List<String> =
        known + (active - known.toSet()).sorted()
}
