package com.muninn

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothServerSocket
import android.bluetooth.BluetoothSocket
import android.content.Intent
import android.os.IBinder
import android.util.Log
import java.util.Collections
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * Foreground service that owns the Bluetooth radio:
 *   - opens an RFCOMM listening socket on the Muninn UUID
 *   - tries connecting to bonded devices that advertise the Muninn UUID
 *   - hands each accepted/connected socket to a PeerSession
 *
 * Milestone 1+ scope. No discovery, no UI peer list, no ConnectionManager
 * yet — those land in milestone 4 (peers.py port).
 */
class MuninnService : Service() {

    private val tag = "MuninnService"
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    private lateinit var bt: Bt
    private lateinit var identity: Identity.Loaded
    private var serverSocket: BluetoothServerSocket? = null
    private var acceptJob: Job? = null
    private var connectJob: Job? = null

    private val sessions = Collections.synchronizedSet(mutableSetOf<PeerSession>())

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        ensureChannel()
        startForeground(NOTIFICATION_ID, buildNotification("starting…"))

        bt = Bt(this)
        identity = Identity.load(this)
        Log.i(tag, "wireMac=${identity.wireMacStr} pubkey=${identity.pubkey.toHex8()}…")

        if (!bt.isReady) {
            Log.w(tag, "Bluetooth not enabled; service will idle")
            updateNotification("Bluetooth disabled")
            return
        }
        startAcceptLoop()
        startConnectLoop()
        updateNotification("listening on Muninn UUID")
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int = START_STICKY

    override fun onDestroy() {
        Log.i(tag, "stopping")
        runCatching { serverSocket?.close() }
        synchronized(sessions) {
            sessions.forEach { it.stop() }
            sessions.clear()
        }
        scope.cancel()
        super.onDestroy()
    }

    private fun startAcceptLoop() {
        acceptJob = scope.launch(Dispatchers.IO) {
            val server = try {
                bt.listen().also { serverSocket = it }
            } catch (e: Throwable) {
                Log.e(tag, "listen() failed: ${e.message}")
                return@launch
            }
            Log.i(tag, "listening for inbound RFCOMM connections")
            while (isActive) {
                val sock = try {
                    server.accept() // blocking
                } catch (e: Throwable) {
                    Log.w(tag, "accept() ended: ${e.message}")
                    break
                }
                Log.i(tag, "accepted connection from ${sock.remoteDevice.address}")
                spawnSession(sock)
            }
        }
    }

    private fun startConnectLoop() {
        connectJob = scope.launch(Dispatchers.IO) {
            while (isActive) {
                // Peers the user explicitly picked in the pairing sheet — dialed
                // directly (insecure RFCOMM needs no bond). Plus any device
                // bonded outside the app that advertises the Muninn UUID.
                val known = KnownPeers.load(this@MuninnService)
                val bondedMuninn = bt.bondedDevices()
                    .filter { deviceAdvertisesMuninn(it) }
                    .map { it.address }
                val targets = (known + bondedMuninn).map { it.uppercase() }.toSet()

                for (addr in targets) {
                    if (!isActive) break
                    if (alreadyConnected(addr)) continue
                    val device = bt.remoteDevice(addr) ?: continue
                    tryConnect(device)
                }
                delay(15_000)
            }
        }
    }

    private fun deviceAdvertisesMuninn(device: BluetoothDevice): Boolean {
        val uuids = device.uuids
        // Empty/unknown cache: Android hasn't browsed SDP for this bonded device
        // yet (or cached an empty result from before the peer was up). Kick an
        // async fetch so the next round learns the real UUIDs, but do NOT dial
        // this round — dialing blindly would RFCOMM-connect to every bonded
        // non-Muninn device (each a long, blocking connect) and stall the loop.
        if (uuids.isNullOrEmpty()) {
            runCatching { device.fetchUuidsWithSdp() }
            return false
        }
        return uuids.any { it.uuid == MUNINN_RFCOMM_UUID }
    }

    private fun alreadyConnected(address: String): Boolean {
        // No peer registry yet — best effort guard against re-dialing the same
        // MAC. Replaced by ConnectionManager in milestone 4.
        synchronized(sessions) {
            return sessions.any { it.remoteAddress.equals(address, ignoreCase = true) }
        }
    }

    private suspend fun tryConnect(device: BluetoothDevice) {
        val sock: BluetoothSocket = try {
            withContext(Dispatchers.IO) { bt.connect(device) }
        } catch (e: Throwable) {
            Log.d(tag, "connect ${device.address} failed: ${e.message}")
            return
        }
        Log.i(tag, "connected to ${device.address}")
        spawnSession(sock)
    }

    private fun spawnSession(sock: BluetoothSocket) {
        val addr = sock.remoteDevice.address
        synchronized(sessions) {
            // Dedup the accept-loop vs connect-loop race: if a session to this
            // peer already exists, drop the newcomer instead of stacking two
            // sockets (which makes both ends churn through teardown).
            if (sessions.any { it.remoteAddress == addr }) {
                Log.i(tag, "already have a session to $addr; closing duplicate socket")
                runCatching { sock.close() }
                return
            }
        }
        val session = PeerSession(
            sock,
            identity,
            scope,
            onClosed = { sessions.remove(it) },
        )
        sessions.add(session)
        session.start()
    }

    // --- notification ---

    private fun ensureChannel() {
        val mgr = getSystemService(NotificationManager::class.java)
        val channel = NotificationChannel(
            CHANNEL_ID,
            "Muninn radio",
            NotificationManager.IMPORTANCE_LOW,
        ).apply { description = "Keeps the Bluetooth socket alive in the background." }
        mgr.createNotificationChannel(channel)
    }

    private fun buildNotification(text: String): Notification =
        Notification.Builder(this, CHANNEL_ID)
            .setContentTitle("Muninn")
            .setContentText(text)
            .setSmallIcon(android.R.drawable.stat_sys_data_bluetooth)
            .setOngoing(true)
            .build()

    private fun updateNotification(text: String) {
        val mgr = getSystemService(NotificationManager::class.java)
        mgr.notify(NOTIFICATION_ID, buildNotification(text))
    }

    companion object {
        private const val CHANNEL_ID = "muninn.radio"
        private const val NOTIFICATION_ID = 1
    }
}

private fun ByteArray.toHex8(): String =
    take(8).joinToString("") { "%02x".format(it.toInt() and 0xFF) }
