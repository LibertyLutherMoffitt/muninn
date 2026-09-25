package com.muninn

import android.app.Service
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothServerSocket
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.IBinder
import android.util.Log
import androidx.core.content.ContextCompat
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
 * Foreground service that owns the Bluetooth radio and feeds the [Mesh]:
 *   - listens for inbound RFCOMM connections on the Muninn UUID
 *   - runs its own inquiry, so peers are found with the app in the background
 *   - dials whatever [DialScheduler] says is worth dialling
 *   - hands every connected socket to the mesh, which does the rest —
 *     handshake, routing, relaying for others, retries
 *
 * The radio follows Bluetooth itself: turn Bluetooth (or airplane mode) off
 * and back on mid-flight and listening, scanning and dialling all resume.
 * Nothing is lost while it is off; the mesh keeps every unsent message.
 */
class MuninnService : Service() {

    private val tag = "MuninnService"
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    private lateinit var bt: Bt
    private lateinit var mesh: Mesh
    private lateinit var discovery: BtDiscovery
    private val scheduler = DialScheduler()
    private lateinit var notifier: Notifier

    private val radioLock = Any()
    private var radioJobs: List<Job> = emptyList()
    @Volatile private var serverSocket: BluetoothServerSocket? = null

    private val bluetoothState = object : BroadcastReceiver() {
        override fun onReceive(ctx: Context, intent: Intent) {
            when (intent.getIntExtra(BluetoothAdapter.EXTRA_STATE, BluetoothAdapter.ERROR)) {
                BluetoothAdapter.STATE_ON -> startRadio()
                BluetoothAdapter.STATE_TURNING_OFF, BluetoothAdapter.STATE_OFF -> stopRadio()
            }
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        notifier = Notifier(this)
        notifier.ensureChannels()
        startForeground(Notifier.RADIO_ID, notifier.radioNotification("Starting…"))

        bt = Bt(this)
        mesh = AppGraph.mesh(this)
        discovery = BtDiscovery(this)
        scheduler.policy = Settings.scanPolicy(this)
        ChatRepository.onConversationRead = { conv -> notifier.clear(conv) }
        Log.i(tag, "wire id ${mesh.localId}")

        watchArrivals()
        startMaintenance()
        ContextCompat.registerReceiver(
            this,
            bluetoothState,
            IntentFilter(BluetoothAdapter.ACTION_STATE_CHANGED),
            ContextCompat.RECEIVER_NOT_EXPORTED,
        )
        if (bt.isReady) startRadio() else notifier.updateRadio(radioStatus())
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        // The activity restarts the service after a settings change; apply it
        // now rather than whenever the process next restarts.
        scheduler.policy = Settings.scanPolicy(this)
        mesh.setDisplayName(AppGraph.displayName(this))
        if (bt.isReady) startRadio()
        return START_STICKY
    }

    override fun onDestroy() {
        Log.i(tag, "stopping")
        runCatching { unregisterReceiver(bluetoothState) }
        stopRadio()
        scope.cancel()
        super.onDestroy()
    }

    // --- Radio on / off ---

    private fun startRadio() {
        synchronized(radioLock) {
            if (radioJobs.any { it.isActive }) return
            Log.i(tag, "radio on")
            runCatching { discovery.start() }
            radioJobs = listOf(watchDiscoveries(), acceptLoop(), connectLoop())
        }
        notifier.updateRadio(radioStatus())
    }

    private fun stopRadio() {
        synchronized(radioLock) {
            if (radioJobs.isEmpty()) return
            Log.i(tag, "radio off")
            radioJobs.forEach { it.cancel() }
            radioJobs = emptyList()
            runCatching { serverSocket?.close() }
            serverSocket = null
            runCatching { discovery.stop() }
        }
        mesh.disconnectAll()
        ChatRepository.refresh()
        notifier.updateRadio(radioStatus())
    }

    // --- Loops ---

    /** Accept inbound connections. Re-listens if the server socket dies. */
    private fun acceptLoop(): Job = scope.launch(Dispatchers.IO) {
        while (isActive) {
            val server = try {
                bt.listen().also { serverSocket = it }
            } catch (e: Throwable) {
                Log.w(tag, "listen() failed: ${e.message}; retrying")
                delay(2_000)
                continue
            }
            while (isActive) {
                val sock = try {
                    server.accept()
                } catch (e: Throwable) {
                    Log.w(tag, "accept() ended: ${e.message}")
                    break
                }
                val address = sock.remoteDevice.address
                // The handshake blocks, so run it off the accept loop.
                launch { mesh.attach(BtLink(sock), address, outbound = false) }
            }
            runCatching { server.close() }
            delay(1_000)
        }
    }

    /**
     * Feed every inquiry result to the scheduler and the presence book.
     *
     * `muninn` means SDP confirmed the service — a hint that promotes the
     * device to a peer. Everything else is a candidate to probe, because
     * Android's SDP cache frequently never resolves for a device we have not
     * connected to, and a blind dial is the only sure test.
     */
    private fun watchDiscoveries(): Job = scope.launch {
        discovery.devices.collect { devices ->
            val now = System.currentTimeMillis()
            for (device in devices) {
                scheduler.saw(device.address, now, isPeer = device.muninn)
                ChatRepository.book.recordSighting(device.address, device.rssi, now, muninn = device.muninn)
            }
            ChatRepository.refresh()
        }
    }

    private fun connectLoop(): Job = scope.launch(Dispatchers.IO) {
        var lastInquiry = 0L
        while (isActive) {
            val policy = scheduler.policy
            val now = System.currentTimeMillis()

            // Peers we can reach by radio address stay dial-worthy whether or
            // not this inquiry saw them; inquiry misses are routine.
            dialableAddresses().forEach(scheduler::markPeer)

            if (now - lastInquiry >= policy.inquiryIntervalMs) {
                lastInquiry = now
                runCatching { discovery.scan() }
            }

            val plan = scheduler.plan(now, mesh::isConnectedTransport)
            for (addr in plan.peers) {
                if (!isActive) break
                dial(addr, probe = false)
            }
            for (addr in plan.probes) {
                if (!isActive) break
                dial(addr, probe = true)
            }
            delay(policy.dialIntervalMs)
        }
    }

    /** Housekeeping: relay retries, and keeping the ongoing notice truthful. */
    private fun startMaintenance() = scope.launch {
        while (isActive) {
            runCatching { mesh.maintain() }
            ChatRepository.refresh()
            notifier.updateRadio(radioStatus())
            delay(MAINTAIN_MS)
        }
    }

    /**
     * Radio addresses worth dialling: ones we have connected to before, the
     * radio addresses behind known phones, and known desktop peers (whose wire
     * id *is* their radio address). A phone's wire id is random and cannot be
     * dialled, so it is left out rather than burning a slow connect on it.
     */
    private fun dialableAddresses(): Set<String> {
        val out = HashSet<String>()
        out += KnownPeers.load(this)
        out += ChatRepository.book.aliases().keys
        out += ChatRepository.book.knownPeers().filter(::isRadioAddress)
        bt.bondedDevices().filter(::advertisesMuninn).forEach { out += it.address.uppercase() }
        return out
    }

    private suspend fun dial(address: String, probe: Boolean) {
        val device = bt.remoteDevice(address) ?: return
        val now = System.currentTimeMillis()
        val sock = try {
            withContext(Dispatchers.IO) { bt.connect(device) }
        } catch (e: Throwable) {
            scheduler.failed(address, now, e.message ?: "connect failed")
            // A headset refusing us is not news; only a device we believe is
            // a peer counts as "nearby, can't connect".
            if (!probe) ChatRepository.book.recordDialFailure(address, e.message ?: "connect failed", now)
            return
        }
        if (withContext(Dispatchers.IO) { mesh.attach(BtLink(sock), address, outbound = true) }) {
            scheduler.succeeded(address)
            // Remember it so we dial it directly next time without waiting on SDP.
            KnownPeers.add(this, address)
        } else {
            scheduler.failed(address, now, "handshake failed")
            if (!probe) ChatRepository.book.recordDialFailure(address, "handshake failed", now)
        }
    }

    // --- Notifications ---

    /**
     * Raise a notification for each arriving message, unless the user is
     * already looking at that conversation (or at the list, where the unread
     * badge says the same thing without the buzz).
     */
    private fun watchArrivals() = scope.launch {
        ChatRepository.arrivals.collect { message ->
            val open = ChatRepository.openConversation
            val watching = ChatRepository.uiVisible && (open == null || open == message.conv)
            if (!watching) notifier.notifyMessage(message)
        }
    }

    /** One line describing what the radio is doing, for the ongoing notice. */
    private fun radioStatus(): String {
        if (!bt.isReady) {
            val waiting = mesh.pendingDeliveries()
            return if (waiting > 0) "Bluetooth is off · $waiting waiting to send" else "Bluetooth is off"
        }
        val statuses = ChatRepository.presence.value
        val connected = statuses.count { it.state == PeerBook.State.CONNECTED }
        val relayed = statuses.count { it.state == PeerBook.State.RELAY }
        val stuck = statuses.count { it.unreachableNearby }
        val waiting = mesh.pendingDeliveries()
        val parts = buildList {
            when {
                connected == 1 -> add("Connected to 1 person")
                connected > 1 -> add("Connected to $connected people")
                stuck > 0 -> add("$stuck nearby, not connecting")
                else -> add("Looking for people nearby")
            }
            if (relayed > 0) add("$relayed via relay")
            if (waiting > 0) add("$waiting waiting to send")
        }
        return parts.joinToString(" · ")
    }

    private fun advertisesMuninn(device: BluetoothDevice): Boolean {
        val uuids = device.uuids
        // Empty cache: Android hasn't browsed SDP for this bonded device yet.
        // Kick an async fetch so a later round learns the real UUIDs, but do
        // not dial blindly now — each non-Muninn device is a slow connect.
        if (uuids.isNullOrEmpty()) {
            runCatching { device.fetchUuidsWithSdp() }
            return false
        }
        return uuids.any { it.uuid == MUNINN_RFCOMM_UUID }
    }

    companion object {
        private const val MAINTAIN_MS = 5_000L

        /**
         * A real radio address, as opposed to a phone's random wire id. Wire
         * ids set the locally-administered bit (Identity.kt); Bluetooth
         * Classic addresses are IEEE-assigned and never do.
         */
        fun isRadioAddress(id: String): Boolean =
            runCatching { (macToBytes(id)[0].toInt() and 0x02) == 0 }.getOrDefault(false)
    }
}
