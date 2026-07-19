package com.tactiq.companion

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.graphics.Path
import android.util.Log
import android.view.accessibility.AccessibilityEvent

/**
 * Turns ring tokens into screen-reader actions (build-plan step 9).
 *
 * Route notes (paper section 3.4): Android's AccessibilityService API is
 * public, so this bridge is viable without platform blessing. Focus
 * movement is delivered as dispatched touch gestures — the same swipes a
 * TalkBack user makes — so TalkBack interprets them natively. BACK uses
 * performGlobalAction, which works regardless of the screen reader.
 *
 * Honest limits, encoded rather than hidden:
 *  - UNDO is app-dependent and not guaranteed (Table 3 says so);
 *  - EMERGENCY must route through the platform's own SOS pathway, which
 *    third-party apps cannot invoke; we log and surface it, nothing more;
 *  - quick actions await the /profiles gesture-map backend.
 *
 * STATUS: scaffold — not yet run against a device with TalkBack.
 */
class TactiqAccessibilityService : AccessibilityService() {

    private val tag = "TactiqA11y"

    override fun onServiceConnected() {
        BleLink.onLine = { line -> if (line.startsWith("TOK,")) onToken(line) }
        BleLink.start(this)
        Log.i(tag, "Tactiq bridge connected; scanning for ring")
    }

    private fun onToken(line: String) {
        val token = line.split(",").getOrNull(1) ?: return
        when (GestureMap.actionFor(token)) {
            GestureMap.Action.NEXT -> swipe(right = true)
            GestureMap.Action.PREVIOUS -> swipe(right = false)
            GestureMap.Action.ACTIVATE -> doubleTap()
            GestureMap.Action.BACK -> performGlobalAction(GLOBAL_ACTION_BACK)
            GestureMap.Action.READ -> swipe(right = true, thenBack = true)
            GestureMap.Action.UNDO -> Log.w(tag, "undo: app-dependent, unmapped")
            GestureMap.Action.QUICK_1, GestureMap.Action.QUICK_2 ->
                Log.i(tag, "$token: awaiting /profiles gesture map")
            GestureMap.Action.EMERGENCY ->
                Log.w(tag, "emergency: platform SOS not third-party invokable")
            null -> Log.w(tag, "unknown token $token")
        }
    }

    /** A TalkBack-style horizontal explore swipe in screen centre. */
    private fun swipe(right: Boolean, thenBack: Boolean = false) {
        val m = resources.displayMetrics
        val y = m.heightPixels / 2f
        val x0 = if (right) m.widthPixels * 0.25f else m.widthPixels * 0.75f
        val x1 = if (right) m.widthPixels * 0.75f else m.widthPixels * 0.25f
        val path = Path().apply { moveTo(x0, y); lineTo(x1, y) }
        val stroke = GestureDescription.StrokeDescription(path, 0, 120)
        dispatchGesture(GestureDescription.Builder()
            .addStroke(stroke).build(), null, null)
        if (thenBack) {
            // READ is approximated as focus-away-and-back until TalkBack
            // exposes "read current" directly; revisit in participatory
            // testing.
            val back = Path().apply { moveTo(x1, y); lineTo(x0, y) }
            dispatchGesture(GestureDescription.Builder()
                .addStroke(GestureDescription.StrokeDescription(back, 300, 120))
                .build(), null, null)
        }
    }

    private fun doubleTap() {
        val m = resources.displayMetrics
        val x = m.widthPixels / 2f
        val y = m.heightPixels / 2f
        val b = GestureDescription.Builder()
        val tap1 = Path().apply { moveTo(x, y); lineTo(x, y) }
        val tap2 = Path().apply { moveTo(x, y); lineTo(x, y) }
        b.addStroke(GestureDescription.StrokeDescription(tap1, 0, 40))
        b.addStroke(GestureDescription.StrokeDescription(tap2, 120, 40))
        dispatchGesture(b.build(), null, null)
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {}
    override fun onInterrupt() {}

    override fun onDestroy() {
        BleLink.stop()
        super.onDestroy()
    }
}
