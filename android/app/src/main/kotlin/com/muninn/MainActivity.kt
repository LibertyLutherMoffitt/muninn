package com.muninn

import android.Manifest
import android.bluetooth.BluetoothManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat

private val REQUIRED_PERMISSIONS = arrayOf(
    Manifest.permission.BLUETOOTH_CONNECT,
    Manifest.permission.BLUETOOTH_SCAN,
    Manifest.permission.BLUETOOTH_ADVERTISE,
    Manifest.permission.POST_NOTIFICATIONS,
)

class MainActivity : ComponentActivity() {

    private lateinit var pairing: Pairing
    private var lastAssociated by mutableStateOf<String?>(null)

    private val permLauncher = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions(),
    ) { results ->
        if (results.values.all { it }) startRadio()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        pairing = Pairing(this) { device -> lastAssociated = device.address }
        setContent {
            MaterialTheme {
                Surface(modifier = Modifier.fillMaxSize()) {
                    Status(
                        onPair = pairing::requestPair,
                        lastAssociated = lastAssociated,
                    )
                }
            }
        }
        ensurePermissions()
    }

    private fun ensurePermissions() {
        val missing = REQUIRED_PERMISSIONS.filter {
            ContextCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED
        }
        if (missing.isEmpty()) startRadio() else permLauncher.launch(missing.toTypedArray())
    }

    private fun startRadio() {
        ContextCompat.startForegroundService(this, Intent(this, MuninnService::class.java))
    }
}

@Composable
private fun Status(onPair: () -> Unit, lastAssociated: String?) {
    val ctx = LocalContext.current
    val mgr = remember { ctx.getSystemService(Context.BLUETOOTH_SERVICE) as? BluetoothManager }
    val adapter = mgr?.adapter
    val adapterText = when {
        adapter == null -> "no Bluetooth adapter"
        !adapter.isEnabled -> "adapter present, disabled"
        else -> "adapter ready"
    }
    val identity = remember { Identity.load(ctx) }
    Column(
        modifier = Modifier.padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Text("Muninn", style = MaterialTheme.typography.headlineMedium)
        Text(adapterText, style = MaterialTheme.typography.bodyMedium)
        Spacer(Modifier.height(4.dp))
        Text("wire id: ${identity.wireMacStr}", style = MaterialTheme.typography.bodySmall)
        Text(
            "pubkey: ${identity.pubkey.joinToString("") { "%02x".format(it.toInt() and 0xFF) }.take(16)}…",
            style = MaterialTheme.typography.bodySmall,
        )
        Spacer(Modifier.height(8.dp))
        Button(onClick = onPair) { Text("Pair a Muninn device") }
        if (lastAssociated != null) {
            Text("paired: $lastAssociated", style = MaterialTheme.typography.bodySmall)
        }
    }
}
