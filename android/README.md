# Tactiq Companion (Android) — build-plan step 9

Kotlin scaffold for the AccessibilityService bridge: `BleLink` subscribes
to the ring's Nordic UART token stream (docs/PROTOCOL.md),
`TactiqAccessibilityService` maps tokens to TalkBack-compatible actions
(dispatched swipes for next/previous, double-tap to activate, global BACK),
and `GestureMap` is the stub that will fetch per-user mappings from
`GET /profiles/{profileId}/gestures`.

**Status: scaffold, not yet built or run.** Open this `android/` folder in
Android Studio (it will supply the Gradle wrapper), build, install, then
enable *Tactiq ring commands* under Settings → Accessibility. Requires a
flashed `tactiq_ring_ble` ring to connect to.
