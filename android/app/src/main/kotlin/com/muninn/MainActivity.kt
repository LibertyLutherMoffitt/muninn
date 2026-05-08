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
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.height
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
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

    private val permLauncher = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions(),
    ) { results ->
        if (results.values.all { it }) startRadio()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme {
                Surface(modifier = Modifier.fillMaxSize()) {
                    Status()
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
private fun Status() {
    val ctx = LocalContext.current
    val mgr = remember { ctx.getSystemService(Context.BLUETOOTH_SERVICE) as? BluetoothManager }
    val adapter = mgr?.adapter
    val adapterText = when {
        adapter == null -> "no Bluetooth adapter"
        !adapter.isEnabled -> "adapter present, disabled"
        else -> "adapter ready"
    }
    val identity = remember { Identity.load(ctx) }
    Column(Modifier.padding(16.dp)) {
        Text("Muninn", style = MaterialTheme.typography.headlineMedium)
        Text(adapterText, style = MaterialTheme.typography.bodyMedium)
        Spacer(Modifier.height(12.dp))
        Text("wire id: ${identity.wireMacStr}", style = MaterialTheme.typography.bodySmall)
        Text(
            "pubkey: ${identity.pubkey.joinToString("") { "%02x".format(it.toInt() and 0xFF) }.take(16)}…",
            style = MaterialTheme.typography.bodySmall,
        )
    }
}
