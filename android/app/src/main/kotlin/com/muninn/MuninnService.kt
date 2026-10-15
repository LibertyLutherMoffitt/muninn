package com.muninn

import android.app.NotificationManager
import android.app.Service
import android.bluetooth.BluetoothAdapter
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.ServiceInfo
import android.os.IBinder
import android.os.PowerManager
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.launch

/**
 * Foreground service that owns the radio: runs the Node (mesh + transport)
 * for as long as the user wants to be reachable, survives the UI going away,
 * and keeps an always-current status notification.
 */
class MuninnService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private var wake: PowerManager.WakeLock? = null

    private val btState = object : BroadcastReceiver() {
        override fun onReceive(ctx: Context, intent: Intent) {
            val s = intent.getIntExtra(BluetoothAdapter.EXTRA_STATE, -1)
            if (s == BluetoothAdapter.STATE_ON) {
                (Runtime.transport as? BtTransport)?.restartListener()
                Runtime.node?.poke()
            }
            update()
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        Notifications.ensureChannels(this)
        try {
            startForeground(Notifications.RADIO_ID, Notifications.radio(this, "starting…"),
                ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE)
        } catch (e: SecurityException) {
            // Bluetooth permission revoked: nothing we can do in the background.
            stopSelf()
            return
        }
        // Node startup touches sockets/disk: never on the main thread.
        Thread({ runCatching { Runtime.start(this) }.onFailure { android.util.Log.e("Muninn", "start failed", it) }; Runtime.bump() },
            "muninn-start").start()
        registerReceiver(btState, IntentFilter(BluetoothAdapter.ACTION_STATE_CHANGED))
        // Partial wakelock only while transfers are running: idle chat is fine
        // under Doze for a foreground service, bulk transfers are not.
        wake = getSystemService(PowerManager::class.java).newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "muninn:transfer")
        scope.launch { Runtime.tick.collectLatest { delay(500); update() } }
    }

    private fun update() {
        val node = Runtime.node ?: return
        val m = node.mesh
        val direct = m.connected().size
        val reach = synchronized(m.lock) { m.peers.keys.toList() }.count { m.reachable(it) }
        val pending = m.pendingCount()
        val bt = (Runtime.transport as? BtTransport)?.isReady ?: true
        val text = when {
            !bt -> "Bluetooth is off"
            direct == 0 -> "Looking for people nearby" + if (pending > 0) " · $pending waiting" else ""
            else -> "$direct nearby · $reach reachable" + if (pending > 0) " · $pending waiting" else ""
        }
        getSystemService(NotificationManager::class.java).notify(Notifications.RADIO_ID, Notifications.radio(this, text))
        val transferring = synchronized(m.lock) { m.downloads.isNotEmpty() }
        wake?.let { if (transferring && !it.isHeld) it.acquire(10 * 60_000L) else if (!transferring && it.isHeld) it.release() }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int = START_STICKY

    override fun onDestroy() {
        runCatching { unregisterReceiver(btState) }
        wake?.let { if (it.isHeld) it.release() }
        scope.cancel()
        Runtime.stop()
        super.onDestroy()
    }
}
