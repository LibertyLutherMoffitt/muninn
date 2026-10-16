package com.muninn

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.core.content.ContextCompat

private val REQUIRED_PERMISSIONS = arrayOf(
    Manifest.permission.BLUETOOTH_CONNECT,
    Manifest.permission.BLUETOOTH_SCAN,
    Manifest.permission.BLUETOOTH_ADVERTISE,
    Manifest.permission.POST_NOTIFICATIONS,
)

sealed class Screen {
    data object Convs : Screen()
    data class Chat(val conv: String) : Screen()
    data object Nearby : Screen()
    data object NewGroup : Screen()
    data class GroupInfo(val conv: String) : Screen()
    data object Settings : Screen()
    data class Game(val session: String) : Screen()
    data class Image(val path: String) : Screen()
    data object Search : Screen()
}

/** Simple back stack (no navigation library needed for a handful of screens). */
object Nav {
    val stack = mutableStateListOf<Screen>(Screen.Convs)
    fun go(s: Screen) { if (stack.lastOrNull() != s) stack.add(s) }
    fun back(): Boolean = if (stack.size > 1) { stack.removeAt(stack.lastIndex); true } else false
    fun reset(s: Screen) { stack.clear(); stack.add(Screen.Convs); if (s != Screen.Convs) stack.add(s) }
}

class MainActivity : ComponentActivity() {
    lateinit var discovery: BtDiscovery

    private val permLauncher = registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) {
        if (hasBt()) startRadio() else Runtime.toast("Muninn needs the Nearby devices permission to use Bluetooth")
    }

    private fun hasBt() = ContextCompat.checkSelfPermission(this, Manifest.permission.BLUETOOTH_CONNECT) ==
        PackageManager.PERMISSION_GRANTED

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        Emoji.init(this)
        discovery = BtDiscovery(this)
        handleIntent(intent)
        setContent {
            MuninnTheme {
                Surface(modifier = Modifier.fillMaxSize()) { App(discovery) }
            }
        }
        ensurePermissions()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleIntent(intent)
    }

    /**
     * Deep links from notifications, plus debug/test hooks usable via adb:
     *   am start -n com.muninn/.MainActivity --es sim 10.0.2.2:7420 --es name kim
     */
    private fun handleIntent(i: Intent?) {
        i ?: return
        val p = Runtime.prefs(this)
        var restart = false
        i.getStringExtra("sim")?.let { p.edit().putString("sim_hub", it).apply(); restart = true }
        i.getStringExtra("pos")?.let { p.edit().putString("sim_pos", it).apply(); restart = true }
        i.getStringExtra("name")?.let { n -> p.edit().putString("name", n).apply(); Runtime.node?.mesh?.setName(n) }
        if (restart) {
            stopService(Intent(this, MuninnService::class.java))
            Runtime.stop()
            startRadio()
        }
        i.getStringExtra("conv")?.let { Nav.reset(Screen.Chat(it)) }
        i.getStringExtra("game")?.let { Nav.go(Screen.Game(it)) }
    }

    override fun onStart() {
        super.onStart()
        Runtime.foreground = true
        discovery.start()
        Runtime.bump()
    }

    override fun onStop() {
        Runtime.foreground = false
        discovery.stop()
        super.onStop()
    }

    private fun ensurePermissions() {
        val missing = REQUIRED_PERMISSIONS.filter {
            ContextCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED
        }
        if (missing.isEmpty()) startRadio() else permLauncher.launch(missing.toTypedArray())
    }

    fun startRadio() {
        if (!hasBt()) return
        ContextCompat.startForegroundService(this, Intent(this, MuninnService::class.java))
    }
}

@Composable
fun App(discovery: BtDiscovery) {
    val ctx = LocalContext.current
    val toast by Runtime.toast.collectAsState()
    LaunchedEffect(toast) {
        toast?.let { Toast.makeText(ctx, it, Toast.LENGTH_SHORT).show(); Runtime.clearToast() }
    }
    BackHandler(enabled = Nav.stack.size > 1) { Nav.back() }
    when (val s = Nav.stack.last()) {
        Screen.Convs -> ConversationsScreen()
        is Screen.Chat -> ChatScreen(s.conv)
        Screen.Nearby -> NearbyScreen(discovery)
        Screen.NewGroup -> NewGroupScreen()
        is Screen.GroupInfo -> GroupInfoScreen(s.conv)
        Screen.Settings -> SettingsScreen()
        is Screen.Game -> GameScreen(s.session)
        is Screen.Image -> ImageScreen(s.path)
        Screen.Search -> SearchScreen()
    }
}
