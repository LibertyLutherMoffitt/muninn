package com.muninn

import android.annotation.SuppressLint
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothManager
import android.bluetooth.BluetoothServerSocket
import android.bluetooth.BluetoothSocket
import android.content.Context

/**
 * Thin wrapper over BluetoothAdapter + RFCOMM helpers. All BT permissions
 * (BLUETOOTH_CONNECT / SCAN) are runtime-granted via MainActivity before the
 * service starts; calls in here assume permission is held.
 */
@SuppressLint("MissingPermission")
class Bt(ctx: Context) {
    private val adapter: BluetoothAdapter? =
        ctx.getSystemService(BluetoothManager::class.java)?.adapter

    val isReady: Boolean get() = adapter?.isEnabled == true

    fun listen(name: String = "Muninn"): BluetoothServerSocket =
        requireNotNull(adapter) { "no Bluetooth adapter" }
            .listenUsingInsecureRfcommWithServiceRecord(name, MUNINN_RFCOMM_UUID)

    fun bondedDevices(): Set<BluetoothDevice> = adapter?.bondedDevices ?: emptySet()

    fun remoteDevice(address: String): BluetoothDevice? =
        runCatching { adapter?.getRemoteDevice(address) }.getOrNull()

    fun connect(device: BluetoothDevice): BluetoothSocket {
        val sock = device.createInsecureRfcommSocketToServiceRecord(MUNINN_RFCOMM_UUID)
        adapter?.cancelDiscovery()
        sock.connect() // blocking, throws on failure
        return sock
    }

    fun cancelDiscovery() {
        adapter?.cancelDiscovery()
    }
}
