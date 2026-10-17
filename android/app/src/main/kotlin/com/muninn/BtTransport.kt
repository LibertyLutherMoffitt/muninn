package com.muninn

import android.annotation.SuppressLint
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothManager
import android.bluetooth.BluetoothServerSocket
import android.bluetooth.BluetoothSocket
import android.content.Context
import android.util.Log
import android.bluetooth.BluetoothClass
import android.bluetooth.BluetoothDevice
import android.content.BroadcastReceiver
import android.content.Intent
import android.content.IntentFilter
import com.muninn.core.Conn
import com.muninn.core.ScanResult
import com.muninn.core.Transport
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.io.InputStream
import java.io.OutputStream
import java.util.UUID

val MUNINN_RFCOMM_UUID: UUID = UUID.fromString("320bcf9c-94fe-46f4-b9bf-83535cafcd55")

/**
 * Bluetooth Classic RFCOMM transport for the Kotlin core.
 *
 * Insecure RFCOMM (no bonding, no pairing prompts between Android devices).
 * Good-citizen rules: we only ever dial addresses the core hands us — peers we
 * already talked to, or devices whose SDP record advertises the Muninn UUID.
 * Background inquiry only happens in find mode (Node.finding, ScanPolicy); unknown
 * devices are only probed if they are phones or computers, never paired.
 */
@SuppressLint("MissingPermission")
class BtTransport(private val ctx: Context) : Transport {
    private val adapter: BluetoothAdapter? = ctx.getSystemService(BluetoothManager::class.java)?.adapter
    @Volatile private var server: BluetoothServerSocket? = null
    @Volatile private var stopped = false

    val isReady: Boolean get() = adapter?.isEnabled == true

    class BtConn(val s: BluetoothSocket) : Conn {
        override val input: InputStream = s.inputStream
        override val output: OutputStream = s.outputStream
        override fun close() { runCatching { s.close() } }
    }

    // Android hides its own MAC from apps (API 31+); peers learn ours from the
    // connection. Only used by the core to avoid dialing itself.
    override fun localAddr(): String = "00:00:00:00:00:00"

    override fun start(onIncoming: (Conn, String) -> Unit) {
        Thread({
            while (!stopped) {
                val a = adapter
                if (a == null || !a.isEnabled) { Thread.sleep(2000); continue }
                val srv = try {
                    a.listenUsingInsecureRfcommWithServiceRecord("Muninn", MUNINN_RFCOMM_UUID).also { server = it }
                } catch (e: Throwable) {
                    Log.w(TAG, "listen failed: ${e.message}"); Thread.sleep(3000); continue
                }
                Log.i(TAG, "listening for RFCOMM")
                while (!stopped) {
                    val sock = try { srv.accept() } catch (e: Throwable) { Log.i(TAG, "accept ended: ${e.message}"); break }
                    onIncoming(BtConn(sock), sock.remoteDevice.address)
                }
                runCatching { srv.close() }
                if (!stopped) Thread.sleep(1000)
            }
        }, "bt-accept").apply { isDaemon = true; start() }
    }

    /** Called when Bluetooth was toggled: re-open the server socket. */
    fun restartListener() { runCatching { server?.close() } }

    override fun stop() {
        stopped = true
        runCatching { server?.close() }
    }

    override fun connect(addr: String): Conn {
        val a = adapter ?: throw java.io.IOException("no Bluetooth")
        if (!a.isEnabled) throw java.io.IOException("Bluetooth off")
        val dev = a.getRemoteDevice(addr)
        val sock = dev.createInsecureRfcommSocketToServiceRecord(MUNINN_RFCOMM_UUID)
        if (a.isDiscovering) a.cancelDiscovery() // inquiry makes connects slow/flaky
        try {
            sock.connect()
        } catch (e: Throwable) {
            runCatching { sock.close() }
            throw java.io.IOException("connect $addr: ${e.message}")
        }
        return BtConn(sock)
    }

    /** One inquiry (~12 s on Android). Kind from Class of Device, Muninn-ness
     *  from cached SDP UUIDs (null = unknown → maybe probe-worthy). */
    override fun scan(durationMs: Long): List<ScanResult> {
        val a = adapter ?: return emptyList()
        if (!a.isEnabled) return emptyList()
        val found = ConcurrentHashMap<String, ScanResult>()
        val done = CountDownLatch(1)
        val rx = object : BroadcastReceiver() {
            override fun onReceive(c: Context, i: Intent) {
                when (i.action) {
                    BluetoothDevice.ACTION_FOUND -> {
                        val d = i.getParcelableExtra(BluetoothDevice.EXTRA_DEVICE, BluetoothDevice::class.java) ?: return
                        val rssi = i.getShortExtra(BluetoothDevice.EXTRA_RSSI, Short.MIN_VALUE).toInt().takeIf { it != Short.MIN_VALUE.toInt() }
                        found[d.address] = ScanResult(d.address, runCatching { d.name }.getOrNull() ?: "", muninnOf(d), rssi, kindOf(d))
                    }
                    BluetoothAdapter.ACTION_DISCOVERY_FINISHED -> done.countDown()
                }
            }
        }
        ctx.registerReceiver(rx, IntentFilter().apply {
            addAction(BluetoothDevice.ACTION_FOUND); addAction(BluetoothAdapter.ACTION_DISCOVERY_FINISHED)
        })
        try {
            if (a.isDiscovering) a.cancelDiscovery()
            a.startDiscovery()
            done.await(maxOf(durationMs, 13_000), TimeUnit.MILLISECONDS)
        } finally {
            runCatching { a.cancelDiscovery() }
            runCatching { ctx.unregisterReceiver(rx) }
        }
        return found.values.toList()
    }

    private fun muninnOf(d: BluetoothDevice): Boolean? {
        val u = d.uuids ?: return null
        if (u.isEmpty()) return null
        return u.any { it.uuid == MUNINN_RFCOMM_UUID }
    }

    private fun kindOf(d: BluetoothDevice): String = when (d.bluetoothClass?.majorDeviceClass) {
        BluetoothClass.Device.Major.PHONE -> "phone"
        BluetoothClass.Device.Major.COMPUTER -> "computer"
        BluetoothClass.Device.Major.AUDIO_VIDEO -> "audio"
        BluetoothClass.Device.Major.PERIPHERAL -> "peripheral"
        BluetoothClass.Device.Major.WEARABLE -> "wearable"
        else -> "other"
    }

    /** Bonded devices whose cached SDP record says Muninn — no radio traffic. */
    override fun discover(): List<Pair<String, String>> =
        adapter?.bondedDevices.orEmpty()
            .filter { d -> d.uuids?.any { it.uuid == MUNINN_RFCOMM_UUID } == true }
            .map { it.address to (runCatching { it.name }.getOrNull() ?: it.address) }

    companion object { private const val TAG = "BtTransport" }
}
