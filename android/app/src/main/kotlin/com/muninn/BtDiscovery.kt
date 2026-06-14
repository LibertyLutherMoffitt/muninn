package com.muninn

import android.annotation.SuppressLint
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.Build
import android.os.ParcelUuid
import android.os.Parcelable
import android.util.Log
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update

/**
 * In-app device discovery + SDP-based Muninn filtering — the "Option C" pair
 * flow that replaces CompanionDeviceManager.
 *
 * Why not CDM: its `BluetoothDeviceFilter.addServiceUuid` matches the 128-bit
 * Muninn UUID against the classic-inquiry EIR, which BlueZ usually omits — so
 * the system sheet came up empty. Classic inquiry only carries the device name
 * and class; the service UUIDs live in SDP and must be fetched per device.
 *
 * So we run our own [BluetoothAdapter.startDiscovery], then call
 * [BluetoothDevice.fetchUuidsWithSdp] on each result. SDP *does* return 128-bit
 * UUIDs, so we can reliably flag which nearby devices speak Muninn and show a
 * clean, correct list. Tapping one calls [bond].
 *
 * Lifecycle: [start]/[stop] register the broadcast receiver (tie to the host
 * activity's STARTED state); [scan]/[stopScan] drive an inquiry cycle.
 */
@SuppressLint("MissingPermission")
class BtDiscovery(private val context: Context) {

    data class Device(
        val address: String,
        val name: String?,
        val rssi: Int?,
        val muninn: Boolean, // SDP confirmed it advertises the Muninn UUID
        val bonded: Boolean,
        val bonding: Boolean = false,
    ) {
        val label: String get() = name?.takeIf { it.isNotBlank() } ?: address
    }

    private val adapter: BluetoothAdapter? =
        context.getSystemService(BluetoothManager::class.java)?.adapter

    private val _devices = MutableStateFlow<List<Device>>(emptyList())
    val devices: StateFlow<List<Device>> = _devices.asStateFlow()

    private val _scanning = MutableStateFlow(false)
    val scanning: StateFlow<Boolean> = _scanning.asStateFlow()

    private var registered = false

    private val receiver = object : BroadcastReceiver() {
        override fun onReceive(ctx: Context, intent: Intent) {
            when (intent.action) {
                BluetoothDevice.ACTION_FOUND -> {
                    val device = intent.deviceExtra() ?: return
                    val rssi = intent
                        .getShortExtra(BluetoothDevice.EXTRA_RSSI, Short.MIN_VALUE)
                        .takeIf { it != Short.MIN_VALUE }
                        ?.toInt()
                    upsert(device, rssi)
                    // Cached UUIDs may already be present from a prior bond/scan.
                    if (device.advertisesMuninn()) markMuninn(device.address)
                }

                BluetoothDevice.ACTION_UUID -> {
                    val device = intent.deviceExtra() ?: return
                    val uuids = intent
                        .getParcelableArrayExtraCompat(BluetoothDevice.EXTRA_UUID)
                        ?.filterIsInstance<ParcelUuid>()
                        .orEmpty()
                    if (uuids.any { it.uuid == MUNINN_RFCOMM_UUID }) markMuninn(device.address)
                }

                BluetoothDevice.ACTION_BOND_STATE_CHANGED -> {
                    val device = intent.deviceExtra() ?: return
                    val state = intent.getIntExtra(
                        BluetoothDevice.EXTRA_BOND_STATE,
                        BluetoothDevice.BOND_NONE,
                    )
                    updateBond(device.address, state)
                }

                BluetoothAdapter.ACTION_DISCOVERY_STARTED -> _scanning.value = true

                BluetoothAdapter.ACTION_DISCOVERY_FINISHED -> {
                    _scanning.value = false
                    // Resolve service UUIDs for everything we found but haven't
                    // confirmed yet — fetchUuidsWithSdp is unreliable mid-inquiry,
                    // so we fan it out only once discovery is done.
                    _devices.value
                        .filterNot { it.muninn }
                        .forEach { resolveUuids(it.address) }
                }
            }
        }
    }

    /** Register receivers. Call when the host UI becomes visible. */
    fun start() {
        if (registered) return
        val filter = IntentFilter().apply {
            addAction(BluetoothDevice.ACTION_FOUND)
            addAction(BluetoothDevice.ACTION_UUID)
            addAction(BluetoothDevice.ACTION_BOND_STATE_CHANGED)
            addAction(BluetoothAdapter.ACTION_DISCOVERY_STARTED)
            addAction(BluetoothAdapter.ACTION_DISCOVERY_FINISHED)
        }
        context.registerReceiver(receiver, filter)
        registered = true
    }

    /** Unregister receivers + cancel any inquiry. Call when the UI hides. */
    fun stop() {
        stopScan()
        if (registered) {
            runCatching { context.unregisterReceiver(receiver) }
            registered = false
        }
    }

    /** Seed the list with bonded peers, then kick off a fresh inquiry. */
    fun scan() {
        val a = adapter ?: return
        seedBonded()
        if (a.isDiscovering) a.cancelDiscovery()
        a.startDiscovery()
    }

    fun stopScan() {
        adapter?.takeIf { it.isDiscovering }?.cancelDiscovery()
        _scanning.value = false
    }

    /** Begin bonding. The connect loop in [MuninnService] dials once bonded. */
    fun bond(address: String) {
        val device = adapter?.getRemoteDevice(address) ?: return
        if (device.bondState == BluetoothDevice.BOND_BONDED) return
        adapter?.cancelDiscovery() // bonding is unreliable during inquiry
        updateBond(address, BluetoothDevice.BOND_BONDING)
        runCatching { device.createBond() }
            .onFailure { Log.w(TAG, "createBond($address) failed: ${it.message}") }
    }

    // --- internal state mutation ---

    private fun seedBonded() {
        val bonded = adapter?.bondedDevices.orEmpty()
        bonded.forEach { upsert(it, rssi = null) }
    }

    private fun upsert(device: BluetoothDevice, rssi: Int?) {
        val name = runCatching { device.name }.getOrNull()
        val bonded = device.bondState == BluetoothDevice.BOND_BONDED
        _devices.update { list ->
            val existing = list.find { it.address == device.address }
            val merged = Device(
                address = device.address,
                name = name ?: existing?.name,
                rssi = rssi ?: existing?.rssi,
                muninn = existing?.muninn == true || device.advertisesMuninn(),
                bonded = bonded,
                bonding = existing?.bonding == true && !bonded,
            )
            if (existing == null) list + merged
            else list.map { if (it.address == device.address) merged else it }
        }
    }

    private fun markMuninn(address: String) =
        _devices.update { list ->
            list.map { if (it.address == address) it.copy(muninn = true) else it }
        }

    private fun updateBond(address: String, state: Int) =
        _devices.update { list ->
            list.map {
                if (it.address != address) it
                else it.copy(
                    bonded = state == BluetoothDevice.BOND_BONDED,
                    bonding = state == BluetoothDevice.BOND_BONDING,
                )
            }
        }

    private fun resolveUuids(address: String) {
        runCatching { adapter?.getRemoteDevice(address)?.fetchUuidsWithSdp() }
    }

    private fun BluetoothDevice.advertisesMuninn(): Boolean =
        uuids?.any { it.uuid == MUNINN_RFCOMM_UUID } == true

    // --- intent extras (API-33 split) ---

    private fun Intent.deviceExtra(): BluetoothDevice? =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            getParcelableExtra(BluetoothDevice.EXTRA_DEVICE, BluetoothDevice::class.java)
        } else {
            @Suppress("DEPRECATION") getParcelableExtra(BluetoothDevice.EXTRA_DEVICE)
        }

    private fun Intent.getParcelableArrayExtraCompat(key: String): Array<out Parcelable>? =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            getParcelableArrayExtra(key, Parcelable::class.java)
        } else {
            @Suppress("DEPRECATION") getParcelableArrayExtra(key)
        }

    companion object {
        private const val TAG = "BtDiscovery"
    }
}
