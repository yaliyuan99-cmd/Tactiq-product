package com.tactiq.companion

import android.annotation.SuppressLint
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothGatt
import android.bluetooth.BluetoothGattCallback
import android.bluetooth.BluetoothGattCharacteristic
import android.bluetooth.BluetoothGattDescriptor
import android.bluetooth.BluetoothProfile
import android.bluetooth.le.ScanCallback
import android.bluetooth.le.ScanFilter
import android.bluetooth.le.ScanResult
import android.bluetooth.le.ScanSettings
import android.content.Context
import android.os.ParcelUuid
import android.util.Log
import java.util.UUID

/**
 * BLE central for the ring's Nordic UART service (docs/PROTOCOL.md).
 *
 * Singleton so the foreground activity and the AccessibilityService share
 * one connection. Caller must hold BLUETOOTH_SCAN / BLUETOOTH_CONNECT
 * runtime permissions before calling [start].
 *
 * STATUS: scaffold — written against the protocol and Android BLE APIs but
 * not yet run against real hardware.
 */
@SuppressLint("MissingPermission")
object BleLink {
    private val NUS_SERVICE: UUID =
        UUID.fromString("6e400001-b5a3-f393-e0a9-e50e24dcca9e")
    private val NUS_TX: UUID =  // ring -> phone, notify
        UUID.fromString("6e400003-b5a3-f393-e0a9-e50e24dcca9e")
    private val CCCD: UUID =
        UUID.fromString("00002902-0000-1000-8000-00805f9b34fb")
    private const val TAG = "TactiqBle"

    /** Called with each complete line from the ring, e.g. "TOK,confirm,..." */
    var onLine: ((String) -> Unit)? = null
    var onState: ((String) -> Unit)? = null

    private var gatt: BluetoothGatt? = null
    private val lineBuffer = StringBuilder()

    fun start(context: Context) {
        val adapter = BluetoothAdapter.getDefaultAdapter() ?: return
        onState?.invoke("scanning")
        val filter = ScanFilter.Builder()
            .setServiceUuid(ParcelUuid(NUS_SERVICE)).build()
        val settings = ScanSettings.Builder()
            .setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY).build()
        adapter.bluetoothLeScanner.startScan(
            listOf(filter), settings, object : ScanCallback() {
                override fun onScanResult(type: Int, result: ScanResult) {
                    adapter.bluetoothLeScanner.stopScan(this)
                    onState?.invoke("connecting to ${result.device.address}")
                    gatt = result.device.connectGatt(
                        context, false, gattCallback)
                }
            })
    }

    fun stop() {
        gatt?.close()
        gatt = null
        onState?.invoke("disconnected")
    }

    private val gattCallback = object : BluetoothGattCallback() {
        override fun onConnectionStateChange(g: BluetoothGatt,
                                             status: Int, newState: Int) {
            if (newState == BluetoothProfile.STATE_CONNECTED) {
                onState?.invoke("connected — discovering services")
                g.discoverServices()
            } else if (newState == BluetoothProfile.STATE_DISCONNECTED) {
                onState?.invoke("disconnected")
            }
        }

        override fun onServicesDiscovered(g: BluetoothGatt, status: Int) {
            val tx = g.getService(NUS_SERVICE)?.getCharacteristic(NUS_TX)
            if (tx == null) {
                onState?.invoke("NUS TX characteristic not found")
                return
            }
            g.setCharacteristicNotification(tx, true)
            tx.getDescriptor(CCCD)?.let { d ->
                d.value = BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE
                g.writeDescriptor(d)
            }
            onState?.invoke("subscribed to ring")
        }

        @Deprecated("pre-T signature kept for minSdk 31")
        override fun onCharacteristicChanged(g: BluetoothGatt,
                                             c: BluetoothGattCharacteristic) {
            val chunk = c.value?.toString(Charsets.US_ASCII) ?: return
            for (ch in chunk) {
                if (ch == '\n') {
                    val line = lineBuffer.toString().trim()
                    lineBuffer.clear()
                    if (line.isNotEmpty()) {
                        Log.d(TAG, "rx: $line")
                        onLine?.invoke(line)
                    }
                } else {
                    lineBuffer.append(ch)
                }
            }
        }
    }
}
