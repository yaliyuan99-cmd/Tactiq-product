package com.tactiq.companion

/**
 * Token -> action mapping (build-plan step 9 integration point).
 *
 * The defaults mirror Table 3. In the product this map is fetched from the
 * website API — GET /profiles/{profileId}/gestures — and device status is
 * reported to /devices; both are stubbed here until the site's backend
 * exists.
 */
object GestureMap {

    enum class Action { NEXT, PREVIOUS, ACTIVATE, BACK, READ, UNDO,
                        QUICK_1, QUICK_2, EMERGENCY }

    private val default = mapOf(
        "next" to Action.NEXT,
        "previous" to Action.PREVIOUS,
        "confirm" to Action.ACTIVATE,
        "back" to Action.BACK,
        "read" to Action.READ,
        "undo" to Action.UNDO,
        "quick_action_1" to Action.QUICK_1,
        "quick_action_2" to Action.QUICK_2,
        "emergency" to Action.EMERGENCY,
    )

    fun actionFor(token: String): Action? = default[token]

    /** TODO: fetch the user's mapping from GET /profiles/{id}/gestures. */
    fun refreshFromServer(profileId: String) {}

    /** TODO: report battery/connection to the site's /devices endpoint. */
    fun reportDeviceStatus(batteryPct: Int, connected: Boolean) {}
}
