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
 *   - runs its own inquiry, so peers are found with the app in the background
 *   - dials whatever [DialScheduler] says is worth dialling
 *   - hands each accepted/connected socket to a PeerSession
 *
 * Discovery lives here rather than in the activity on purpose: a peer who sits
 * down near you has to be picked up with the screen off, which is the whole
 * point of the app. The pairing sheet in the UI is now a manual fallback for
 * when SDP is uncooperative, not the only way in.
 */
class MuninnService : Service() {

    private val tag = "MuninnService"
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    private lateinit var bt: Bt
    private lateinit var identity: Identity.Loaded
    private lateinit var discovery: BtDiscovery
    private val scheduler = DialScheduler()
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
        discovery = BtDiscovery(this)
        scheduler.policy = Settings.scanPolicy(this)
        ChatRepository.book.knownPeers().forEach(scheduler::markPeer)
        KnownPeers.load(this).forEach(scheduler::markPeer)
        Log.i(tag, "wireMac=${identity.wireMacStr} pubkey=${identity.pubkey.toHex8()}…")

        if (!bt.isReady) {
            Log.w(tag, "Bluetooth not enabled; service will idle")
            updateNotification("Bluetooth disabled")
            return
        }
        discovery.start()
        watchDiscoveries()
        startAcceptLoop()
        startConnectLoop()
        updateNotification("listening on Muninn UUID")
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int = START_STICKY

    override fun onDestroy() {
        Log.i(tag, "stopping")
        runCatching { discovery.stop() }
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

    /**
     * Feed every inquiry result to the scheduler.
     *
     * `muninn` means SDP confirmed the service — a hint that promotes the
     * device to a peer. Everything else is a candidate to probe, because
     * Android's SDP cache frequently never resolves for a device we have not
     * connected to, and a blind dial is the only sure test.
     */
    private fun watchDiscoveries() {
        scope.launch {
            discovery.devices.collect { devices ->
                val now = System.currentTimeMillis()
                for (device in devices) {
                    scheduler.saw(device.address, now, isPeer = device.muninn)
                    ChatRepository.book.recordSighting(device.address, device.rssi, now)
                }
                ChatRepository.refreshPresence()
            }
        }
    }

    private fun startConnectLoop() {
        connectJob = scope.launch(Dispatchers.IO) {
            var lastInquiry = 0L
            while (isActive) {
                val policy = scheduler.policy
                val now = System.currentTimeMillis()

                // Peers we already hold keys for stay dial-worthy whether or
                // not this inquiry saw them; inquiry misses are routine.
                KnownPeers.load(this@MuninnService).forEach(scheduler::markPeer)
                ChatRepository.book.knownPeers().forEach(scheduler::markPeer)
                bt.bondedDevices()
                    .filter { deviceAdvertisesMuninn(it) }
                    .forEach { scheduler.markPeer(it.address) }

                if (now - lastInquiry >= policy.inquiryIntervalMs) {
                    lastInquiry = now
                    runCatching { discovery.scan() }
                }

                val plan = scheduler.plan(now, ::alreadyConnected)
                for (addr in plan.peers) {
                    if (!isActive) break
                    dial(addr, probe = false)
                }
                for (addr in plan.probes) {
                    if (!isActive) break
                    dial(addr, probe = true)
                }
                ChatRepository.refreshPresence()
                delay(policy.dialIntervalMs)
            }
        }
    }

    private suspend fun dial(address: String, probe: Boolean) {
        val device = bt.remoteDevice(address) ?: return
        val now = System.currentTimeMillis()
        val sock: BluetoothSocket = try {
            withContext(Dispatchers.IO) { bt.connect(device) }
        } catch (e: Throwable) {
            scheduler.failed(address, now, e.message ?: "connect failed")
            // A headset refusing us is not news; only report a device we
            // believe is a peer as unreachable.
            if (!probe) {
                ChatRepository.book.recordDialFailure(address, e.message ?: "connect failed", now)
            }
            return
        }
        Log.i(tag, "connected to $address")
        scheduler.succeeded(address)
        // Remember it so we dial it directly next time without waiting on SDP.
        KnownPeers.add(this, address)
        spawnSession(sock)
    }

    /** Change how hard to hunt, and remember the choice. */
    fun setScanPolicy(policy: ScanPolicy) {
        Settings.setScanPolicy(this, policy)
        scheduler.policy = policy
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
            ChatRepository.book,
            scope,
            displayName = deviceDisplayName(),
            onClosed = {
                sessions.remove(it)
                ChatRepository.refreshPresence()
            },
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

    /**
     * The name peers see. The Bluetooth adapter name is what the user already
     * chose for this phone, so it needs no separate setting.
     */
    private fun deviceDisplayName(): String =
        runCatching { android.provider.Settings.Global.getString(contentResolver, "device_name") }
            .getOrNull()
            ?.takeIf { it.isNotBlank() }
            ?: android.os.Build.MODEL
            ?: ""

    companion object {
        private const val CHANNEL_ID = "muninn.radio"
        private const val NOTIFICATION_ID = 1
    }
}

private fun ByteArray.toHex8(): String =
    take(8).joinToString("") { "%02x".format(it.toInt() and 0xFF) }
