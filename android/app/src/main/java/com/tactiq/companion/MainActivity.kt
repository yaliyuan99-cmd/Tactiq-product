package com.tactiq.companion

import android.Manifest
import android.os.Bundle
import android.provider.Settings
import android.content.Intent
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat

/**
 * Minimal status screen: request BLE permissions, point the user to the
 * accessibility settings to enable the bridge, and show the live token
 * stream for debugging. Deliberately spartan — the real interaction
 * surface is the ring + screen reader, not this app (P1).
 */
class MainActivity : AppCompatActivity() {

    private lateinit var log: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(48, 48, 48, 48)
        }
        val status = TextView(this).apply { text = "Ring: not connected" }
        val enable = Button(this).apply {
            text = "Enable Tactiq in Accessibility settings"
            setOnClickListener {
                startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
            }
        }
        log = TextView(this)
        root.addView(status)
        root.addView(enable)
        root.addView(ScrollView(this).apply { addView(log) })
        setContentView(root)

        ActivityCompat.requestPermissions(this, arrayOf(
            Manifest.permission.BLUETOOTH_SCAN,
            Manifest.permission.BLUETOOTH_CONNECT), 1)

        BleLink.onState = { s -> runOnUiThread { status.text = "Ring: $s" } }
        BleLink.onLine = { l -> runOnUiThread { append(l) } }
    }

    private fun append(line: String) {
        log.text = (line + "\n" + log.text).take(4000)
    }
}
