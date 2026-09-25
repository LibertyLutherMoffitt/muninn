package com.muninn

import android.Manifest
import android.bluetooth.BluetoothAdapter
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.util.Log
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInHorizontally
import androidx.compose.animation.slideOutHorizontally
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Surface
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.MutableState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.core.content.ContextCompat
import com.muninn.ui.ChatScreen
import com.muninn.ui.ConversationListScreen
import com.muninn.ui.NameDialog
import com.muninn.ui.NewGroupDialog
import com.muninn.ui.PairingSheet
import com.muninn.ui.PeersSheet
import com.muninn.ui.ScanModeDialog

private val REQUIRED_PERMISSIONS = arrayOf(
    Manifest.permission.BLUETOOTH_CONNECT,
    Manifest.permission.BLUETOOTH_SCAN,
    Manifest.permission.BLUETOOTH_ADVERTISE,
    Manifest.permission.POST_NOTIFICATIONS,
)

/**
 * The activity: permissions, lifecycle, discoverability, and telling
 * [ChatRepository] what is on screen (which is how the service knows when a
 * notification would be noise). Everything drawn lives in `com.muninn.ui`.
 */
class MainActivity : ComponentActivity() {

    private lateinit var discovery: BtDiscovery

    // A conversation to open, from a tapped notification.
    private val requestedConversation: MutableState<String?> = mutableStateOf(null)

    private val permLauncher = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions(),
    ) { results ->
        if (results.values.all { it }) startRadio()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        // The mesh (and with it history) exists before the radio does, so a
        // message typed before permission is granted is still kept.
        AppGraph.mesh(this)
        discovery = BtDiscovery(this)
        requestedConversation.value = intent.getStringExtra(Notifier.EXTRA_CONVERSATION)
        setContent {
            MuninnTheme {
                Surface(modifier = Modifier.fillMaxSize()) {
                    MuninnApp(discovery, requestedConversation)
                }
            }
        }
        ensurePermissions()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        intent.getStringExtra(Notifier.EXTRA_CONVERSATION)?.let { requestedConversation.value = it }
    }

    override fun onStart() {
        super.onStart()
        discovery.start()
        ChatRepository.uiVisible = true
        ChatRepository.openConversation?.let(ChatRepository::markConversationRead)
    }

    override fun onStop() {
        ChatRepository.uiVisible = false
        discovery.stop()
        super.onStop()
    }

    private fun ensurePermissions() {
        val missing = REQUIRED_PERMISSIONS.filter {
            ContextCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED
        }
        if (missing.isEmpty()) startRadio() else permLauncher.launch(missing.toTypedArray())
    }

    /**
     * Ask Android to make this phone findable.
     *
     * Without it the phone answers connections but never appears in anyone
     * else's inquiry, so a desktop can never make first contact — only the
     * phone could ever start a conversation. Android caps the window (300s is
     * the documented maximum on most builds), and only the user can grant it.
     */
    fun requestDiscoverable(seconds: Int = 300) {
        val intent = Intent(BluetoothAdapter.ACTION_REQUEST_DISCOVERABLE).apply {
            putExtra(BluetoothAdapter.EXTRA_DISCOVERABLE_DURATION, seconds)
        }
        runCatching { startActivity(intent) }
            .onFailure { Log.w("MainActivity", "discoverable request refused: ${it.message}") }
    }

    fun startRadio() {
        ContextCompat.startForegroundService(this, Intent(this, MuninnService::class.java))
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun MuninnApp(discovery: BtDiscovery, requested: MutableState<String?>) {
    val ctx = LocalContext.current
    val activity = ctx as? MainActivity
    var open by rememberSaveable { mutableStateOf<String?>(null) }
    var showPeers by remember { mutableStateOf(false) }
    var showPairing by remember { mutableStateOf(false) }
    var showScanMode by remember { mutableStateOf(false) }
    var showName by remember { mutableStateOf(false) }
    var showNewGroup by remember { mutableStateOf(false) }
    var scanPolicy by remember { mutableStateOf(Settings.scanPolicy(ctx)) }

    // A tapped notification wins over whatever was on screen.
    LaunchedEffect(requested.value) {
        requested.value?.let {
            open = it
            requested.value = null
        }
    }
    // Tell the repository what is on screen: that is what turns an arrival
    // into a read receipt instead of a notification.
    LaunchedEffect(open) {
        ChatRepository.openConversation = open
        open?.let(ChatRepository::markConversationRead)
    }
    BackHandler(enabled = open != null) { open = null }

    AnimatedContent(
        targetState = open,
        transitionSpec = {
            if (targetState != null) {
                (slideInHorizontally { it / 3 } + fadeIn()) togetherWith fadeOut()
            } else {
                fadeIn() togetherWith (slideOutHorizontally { it / 3 } + fadeOut())
            }
        },
        label = "screen",
    ) { conv ->
        if (conv == null) {
            ConversationListScreen(
                onOpen = { open = it },
                onNewGroup = { showNewGroup = true },
                onShowPeers = { showPeers = true },
                onPair = { showPairing = true },
                onScanMode = { showScanMode = true },
                onYourName = { showName = true },
                scanPolicy = scanPolicy,
            )
        } else {
            ChatScreen(conv = conv, onBack = { open = null })
        }
    }

    if (showPeers) {
        ModalBottomSheet(
            onDismissRequest = { showPeers = false },
            sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
        ) {
            PeersSheet(
                onOpen = {
                    showPeers = false
                    open = ChatRepository.dmId(it)
                },
            )
        }
    }

    if (showPairing) {
        ModalBottomSheet(
            onDismissRequest = {
                discovery.stopScan()
                showPairing = false
            },
            sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
        ) {
            PairingSheet(discovery)
        }
    }

    if (showScanMode) {
        ScanModeDialog(
            current = scanPolicy,
            onDismiss = { showScanMode = false },
            onPick = { picked ->
                scanPolicy = picked
                Settings.setScanPolicy(ctx, picked)
                // The running service re-reads settings on every start command.
                activity?.startRadio()
                showScanMode = false
            },
            onMakeDiscoverable = {
                activity?.requestDiscoverable()
                showScanMode = false
            },
        )
    }

    if (showName) {
        NameDialog(
            title = "Your name",
            explanation = "What people nearby see. Leave it empty to use this phone's name.",
            initial = Settings.displayName(ctx),
            placeholder = AppGraph.displayName(ctx),
            onDismiss = { showName = false },
            onSave = {
                AppGraph.setDisplayName(ctx, it)
                showName = false
            },
        )
    }

    if (showNewGroup) {
        NewGroupDialog(
            onDismiss = { showNewGroup = false },
            onCreate = { name, members ->
                showNewGroup = false
                ChatRepository.createGroup(name, members)?.let { open = it }
            },
        )
    }
}

// --- helpers ---

internal fun formatTime(ts: Long): String =
    android.text.format.DateFormat.format("HH:mm", ts).toString()

/** "14:05" today, "Tue" this week, "3 Mar" before that. For list rows. */
internal fun formatWhen(ts: Long, now: Long = System.currentTimeMillis()): String {
    val day = java.util.Calendar.getInstance()
    fun dayOf(ms: Long): Int {
        day.timeInMillis = ms
        return day.get(java.util.Calendar.YEAR) * 1000 + day.get(java.util.Calendar.DAY_OF_YEAR)
    }
    return when {
        dayOf(ts) == dayOf(now) -> formatTime(ts)
        now - ts < 6 * 24 * 3600 * 1000L -> android.text.format.DateFormat.format("EEE", ts).toString()
        else -> android.text.format.DateFormat.format("d MMM", ts).toString()
    }
}

/** Short, readable form of a wire id. */
internal fun String.shortId(): String = takeLast(8)

/** Delivery state as the desktop client renders it: sent, received, read. */
internal fun ChatRepository.Ack.tick(): String = when (this) {
    ChatRepository.Ack.SENT -> "·"
    ChatRepository.Ack.ACKED -> "✓"
    ChatRepository.Ack.READ -> "✓✓"
}
