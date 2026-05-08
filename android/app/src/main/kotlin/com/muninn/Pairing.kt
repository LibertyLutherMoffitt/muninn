package com.muninn

import android.annotation.SuppressLint
import android.app.Activity
import android.bluetooth.BluetoothDevice
import android.companion.AssociationRequest
import android.companion.BluetoothDeviceFilter
import android.companion.CompanionDeviceManager
import android.content.IntentSender
import android.os.Build
import android.os.ParcelUuid
import android.util.Log
import androidx.activity.ComponentActivity
import androidx.activity.result.IntentSenderRequest
import androidx.activity.result.contract.ActivityResultContracts

/**
 * CompanionDeviceManager wrapper — closest UX to Linux's silent pair that
 * Android grants a non-system app:
 *
 *   1. App calls `requestPair()`.
 *   2. System sheet lists nearby BT Classic devices that advertise the
 *      Muninn RFCOMM UUID (filtered by SDP service-uuid match).
 *   3. User taps one. CDM grants the app a durable association and the
 *      device gets bonded — Android may still show a single Just-Works
 *      confirm here, which is the floor without BLUETOOTH_PRIVILEGED.
 *   4. From then on, our `MuninnService.connectLoop` picks the device up
 *      via `bondedDevices()` and dials it without further UI.
 *
 * Must be constructed *before* the host activity reaches STARTED — registers
 * an activity-result launcher in its constructor.
 */
class Pairing(
    private val activity: ComponentActivity,
    private val onAssociated: (BluetoothDevice) -> Unit = {},
) {
    private val tag = "Pairing"
    private val cdm: CompanionDeviceManager =
        activity.getSystemService(CompanionDeviceManager::class.java)

    private val launcher = activity.registerForActivityResult(
        ActivityResultContracts.StartIntentSenderForResult(),
    ) { result ->
        if (result.resultCode != Activity.RESULT_OK) {
            Log.i(tag, "picker dismissed (rc=${result.resultCode})")
            return@registerForActivityResult
        }
        val device = extractDevice(result.data)
        if (device == null) {
            Log.w(tag, "result missing EXTRA_DEVICE")
            return@registerForActivityResult
        }
        Log.i(tag, "associated ${device.address}; bondState=${device.bondState}")
        bondIfNeeded(device)
        onAssociated(device)
    }

    fun requestPair() {
        val filter = BluetoothDeviceFilter.Builder()
            .addServiceUuid(ParcelUuid(MUNINN_RFCOMM_UUID), null)
            .build()
        val req = AssociationRequest.Builder()
            .addDeviceFilter(filter)
            .setSingleDevice(false)
            .build()

        @Suppress("DEPRECATION") // pre-API-33 form, still works on 31-34
        cdm.associate(req, object : CompanionDeviceManager.Callback() {
            override fun onAssociationPending(sender: IntentSender) = launch(sender)
            override fun onDeviceFound(sender: IntentSender) = launch(sender)
            override fun onFailure(error: CharSequence?) {
                Log.w(tag, "associate failed: $error")
            }
        }, null)
    }

    private fun launch(sender: IntentSender) {
        launcher.launch(IntentSenderRequest.Builder(sender).build())
    }

    @Suppress("DEPRECATION")
    private fun extractDevice(intent: android.content.Intent?): BluetoothDevice? {
        intent ?: return null
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            intent.getParcelableExtra(CompanionDeviceManager.EXTRA_DEVICE, BluetoothDevice::class.java)
        } else {
            intent.getParcelableExtra(CompanionDeviceManager.EXTRA_DEVICE)
        }
    }

    @SuppressLint("MissingPermission")
    private fun bondIfNeeded(device: BluetoothDevice) {
        if (device.bondState == BluetoothDevice.BOND_BONDED) return
        try {
            device.createBond()
        } catch (e: SecurityException) {
            Log.w(tag, "createBond denied: ${e.message}")
        }
    }
}
