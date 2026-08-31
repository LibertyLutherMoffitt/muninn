package com.muninn

import android.Manifest
import android.bluetooth.BluetoothAdapter
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.util.Log
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.ime
import androidx.compose.foundation.layout.navigationBars
import androidx.compose.foundation.layout.union
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.foundation.lazy.itemsIndexed
import com.muninn.ui.Composer
import com.muninn.ui.MessageBubble
import com.muninn.ui.PairingSheet
import com.muninn.ui.ScanModeDialog
import com.muninn.ui.DayDivider
import com.muninn.ui.EmptyState
import com.muninn.ui.PeersSheet
import com.muninn.ui.PresenceDot
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.setValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat

private val REQUIRED_PERMISSIONS = arrayOf(
    Manifest.permission.BLUETOOTH_CONNECT,
    Manifest.permission.BLUETOOTH_SCAN,
    Manifest.permission.BLUETOOTH_ADVERTISE,
    Manifest.permission.POST_NOTIFICATIONS,
)

class MainActivity : ComponentActivity() {

    private lateinit var discovery: BtDiscovery

    private val permLauncher = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions(),
    ) { results ->
        if (results.values.all { it }) startRadio()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        discovery = BtDiscovery(this)
        setContent {
            MuninnTheme {
                Surface(modifier = Modifier.fillMaxSize()) {
                    ChatScreen(discovery = discovery)
                }
            }
        }
        ensurePermissions()
    }

    override fun onStart() {
        super.onStart()
        discovery.start()
    }

    override fun onStop() {
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
     * the documented maximum on most builds), and only the user can grant it,
     * so this is offered rather than nagged: the button in the top bar.
     */
    fun requestDiscoverable(seconds: Int = 300) {
        val intent = Intent(BluetoothAdapter.ACTION_REQUEST_DISCOVERABLE).apply {
            putExtra(BluetoothAdapter.EXTRA_DISCOVERABLE_DURATION, seconds)
        }
        runCatching { startActivity(intent) }
            .onFailure { Log.w("MainActivity", "discoverable request refused: ${it.message}") }
    }

    private fun startRadio() {
        ContextCompat.startForegroundService(this, Intent(this, MuninnService::class.java))
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ChatScreen(discovery: BtDiscovery) {
    val ctx = LocalContext.current
    val identity = remember { Identity.load(ctx) }
    val messages by ChatRepository.messages.collectAsState()
    val peers by ChatRepository.peers.collectAsState()
    val presence by ChatRepository.presence.collectAsState()
    var draft by remember { mutableStateOf("") }
    var showPairing by remember { mutableStateOf(false) }
    var showScanMode by remember { mutableStateOf(false) }
    var showPeers by remember { mutableStateOf(false) }
    var scanPolicy by remember { mutableStateOf(Settings.scanPolicy(ctx)) }
    val listState = rememberLazyListState()

    LaunchedEffect(messages.size) {
        if (messages.isNotEmpty()) listState.animateScrollToItem(messages.size - 1)
        // The conversation is on screen, so anything that just arrived has been
        // presented — that is what separates a READ from the ACK the receive
        // loop already sent.
        ChatRepository.markConversationRead()
    }

    val connected = peers.isNotEmpty()
    // Devices the radio can see but that refuse every connection. Worth saying
    // out loud: it usually means the peer needs to open the app or turn
    // Bluetooth on, which the user can act on.
    val unreachable = presence.filter { it.unreachableNearby }
    val statusText = when {
        connected && peers.size == 1 ->
            "Connected to ${ChatRepository.displayName(peers.first())}"
        connected -> "${peers.size} peers connected"
        unreachable.size == 1 ->
            "${ChatRepository.displayName(unreachable.first().wireId)} is nearby but won't connect"
        unreachable.isNotEmpty() -> "${unreachable.size} devices nearby, none connecting"
        else -> "Not connected · you are ${identity.wireMacStr}"
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text("Muninn", fontWeight = FontWeight.SemiBold)
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            PresenceDot(
                                if (connected) PeerBook.State.CONNECTED
                                else PeerBook.State.OFFLINE,
                                unreachable = unreachable.isNotEmpty(),
                                size = 8.dp,
                            )
                            Text(
                                statusText,
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis,
                            )
                        }
                    }
                },
                actions = {
                    TextButton(onClick = { showPeers = true }) {
                        Text(if (presence.isEmpty()) "Peers" else "Peers (${presence.size})")
                    }
                    TextButton(onClick = { showScanMode = true }) { Text(scanPolicy.label) }
                    TextButton(onClick = { showPairing = true }) { Text("Pair") }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.surface,
                ),
            )
        },
        bottomBar = {
            Composer(
                draft = draft,
                onDraftChange = { draft = it },
                enabled = connected,
                onSend = { if (ChatRepository.send(draft)) draft = "" },
                // union (per-side max), not a chain (sum): when the keyboard is
                // up, WindowInsets.ime already covers the nav-bar region, so
                // stacking navigationBarsPadding().imePadding() would add an
                // extra nav-bar height and shove the screen up too far.
                modifier = Modifier.windowInsetsPadding(
                    WindowInsets.navigationBars.union(WindowInsets.ime),
                ),
            )
        },
        containerColor = MaterialTheme.colorScheme.background,
    ) { innerPadding ->
        if (messages.isEmpty()) {
            EmptyState(
                glyph = if (connected) "\u2709" else "\u25CC",
                title = if (connected) "Say hello" else "Looking for people nearby",
                subtitle = when {
                    connected ->
                        "Messages are encrypted end to end and go straight over " +
                            "Bluetooth \u2014 no network, no account."
                    unreachable.isNotEmpty() ->
                        "Something is in range but will not connect. They may need " +
                            "to open Muninn, or turn Bluetooth on."
                    else ->
                        "Muninn keeps scanning in the background. Anyone running it " +
                            "in range shows up on their own."
                },
                hint = if (connected) null else "Tap Peers to see what is around",
                modifier = Modifier.padding(innerPadding),
            )
        } else {
            LazyColumn(
                state = listState,
                modifier = Modifier
                    .fillMaxSize()
                    .padding(innerPadding),
                contentPadding = PaddingValues(horizontal = 12.dp, vertical = 8.dp),
                verticalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                itemsIndexed(messages) { index, msg ->
                    val previous = messages.getOrNull(index - 1)
                    // Day dividers and runs come from one shared rule set so
                    // the phone groups a thread the way the desktop does.
                    val day = MessageGrouping.daySection(msg.timestamp)
                    if (previous == null ||
                        MessageGrouping.daySection(previous.timestamp) != day
                    ) {
                        DayDivider(day)
                    }
                    MessageBubble(
                        msg,
                        startsRun = MessageGrouping.startsRun(msg, previous),
                        senderName = ChatRepository.displayName(msg.peer),
                    )
                }
            }
        }
    }

    if (showPeers) {
        val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
        ModalBottomSheet(
            onDismissRequest = { showPeers = false },
            sheetState = sheetState,
        ) {
            PeersSheet(presence)
        }
    }

    if (showScanMode) {
        ScanModeDialog(
            current = scanPolicy,
            onDismiss = { showScanMode = false },
            onPick = { picked ->
                scanPolicy = picked
                Settings.setScanPolicy(ctx, picked)
                // Restart the service so the running loop picks it up now
                // rather than after the old, longer waits expire.
                ContextCompat.startForegroundService(
                    ctx, Intent(ctx, MuninnService::class.java)
                )
                showScanMode = false
            },
            onMakeDiscoverable = {
                (ctx as? MainActivity)?.requestDiscoverable()
                showScanMode = false
            },
        )
    }

    if (showPairing) {
        val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
        ModalBottomSheet(
            onDismissRequest = {
                discovery.stopScan()
                showPairing = false
            },
            sheetState = sheetState,
        ) {
            PairingSheet(discovery)
        }
    }
}




// --- pairing bottom sheet ---




// --- helpers ---

internal fun formatTime(ts: Long): String =
    android.text.format.DateFormat.format("HH:mm", ts).toString()

/** Short, readable form of a wire id for the status line. */
internal fun String.shortId(): String = takeLast(8)

/** Delivery state as the desktop client renders it: sent, received, read. */
internal fun ChatRepository.Ack.tick(): String = when (this) {
    ChatRepository.Ack.SENT -> "·"
    ChatRepository.Ack.ACKED -> "\u2713"
    ChatRepository.Ack.READ -> "\u2713\u2713"
}
