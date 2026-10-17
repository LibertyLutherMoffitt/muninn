package com.muninn

import android.annotation.SuppressLint
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothManager
import android.content.Intent
import android.net.Uri
import android.os.PowerManager
import android.provider.Settings
import android.webkit.JavascriptInterface
import android.webkit.WebView
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.Button
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import com.muninn.core.Json
import com.muninn.core.ScanResult
import com.muninn.core.convGroupId
import com.muninn.core.dmConv
import com.muninn.core.groupConv
import com.muninn.core.hex
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SimpleScaffold(title: String, actions: @Composable () -> Unit = {}, content: @Composable (Modifier) -> Unit) {
    Scaffold(topBar = {
        TopAppBar(
            title = { Text(title) },
            navigationIcon = { IconButton(onClick = { Nav.back() }) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back") } },
            actions = { actions() },
        )
    }) { pad -> content(Modifier.padding(pad)) }
}

// ---------------------------------------------------------------- scores

fun scoreLine(g: com.muninn.core.GameHost.Score?): String {
    g ?: return ""
    val m = Runtime.node?.mesh
    val parts = g.wins.entries.sortedByDescending { it.value }
        .map { (n, w) -> (if (n == m?.nodeId) "you" else m?.displayName(n) ?: n) + " $w" }.toMutableList()
    if (g.draws > 0) parts += "${g.draws} draw" + if (g.draws > 1) "s" else ""
    return parts.joinToString(" · ")
}

// ---------------------------------------------------------------- backup

@Composable
fun BackupCard() {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    var password by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    val save = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("application/zip")) { uri ->
        uri ?: return@rememberLauncherForActivityResult
        busy = true
        scope.launch(Dispatchers.IO) {
            runCatching { Backup.export(ctx, uri, password.ifEmpty { null }) }
                .onSuccess { (m, f) -> Runtime.toast("Saved $m messages and $f files" + if (password.isNotEmpty()) " (with your identity)" else "") }
                .onFailure { Runtime.toast("Backup failed: ${it.message}") }
            busy = false
        }
    }
    val open = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        uri ?: return@rememberLauncherForActivityResult
        busy = true
        scope.launch(Dispatchers.IO) {
            runCatching { Backup.import(ctx, uri, password.ifEmpty { null }) }
                .onSuccess { r -> Runtime.toast("Added ${r.messages} messages, ${r.people} people, ${r.rooms} rooms" +
                    if (r.identity) " — you are now ${Runtime.node?.mesh?.name}" else "") }
                .onFailure { Runtime.toast("Import failed: ${it.message}") }
            busy = false
        }
    }
    Text("Backup", style = MaterialTheme.typography.titleSmall)
    Text("Save your chats and files to a file, or bring them back (also from the desktop app). With a password the " +
        "backup also carries your identity, so a new phone can continue as you.",
        style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    OutlinedTextField(password, { password = it }, singleLine = true, label = { Text("Password (optional)") },
        visualTransformation = androidx.compose.ui.text.input.PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth())
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
        OutlinedButton(enabled = !busy, onClick = {
            save.launch("muninn-backup-" + java.text.SimpleDateFormat("yyyyMMdd-HHmm", java.util.Locale.US).format(java.util.Date()) + ".zip")
        }) { Text("Back up") }
        OutlinedButton(enabled = !busy, onClick = { open.launch(arrayOf("application/zip", "application/octet-stream", "*/*")) }) { Text("Restore…") }
        if (busy) CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp)
    }
}

// ---------------------------------------------------------------- before the flight

/** Pre-flight check (mirrors node.doctor on desktop): everything that keeps
 *  messages flowing in the air, at a glance. */
@SuppressLint("BatteryLife", "MissingPermission")
@Composable
fun DoctorCard() {
    val ctx = LocalContext.current
    val tick by Runtime.tick.collectAsState()
    val rows by produceState(emptyList<Triple<String, String, String>>(), tick) {
        value = withContext(Dispatchers.IO) {
            val out = ArrayList<Triple<String, String, String>>()
            val adapter = ctx.getSystemService(BluetoothManager::class.java)?.adapter
            out += if (adapter?.isEnabled == true) Triple("ok", "Bluetooth", "on") else Triple("bad", "Bluetooth", "off — turn it on")
            val perms = listOf(android.Manifest.permission.BLUETOOTH_CONNECT, android.Manifest.permission.BLUETOOTH_SCAN,
                android.Manifest.permission.POST_NOTIFICATIONS)
            val missing = perms.filter { ctx.checkSelfPermission(it) != android.content.pm.PackageManager.PERMISSION_GRANTED }
            out += if (missing.isEmpty()) Triple("ok", "Permissions", "all granted")
                   else Triple("bad", "Permissions", "missing: " + missing.joinToString { it.substringAfterLast('.') })
            val pm = ctx.getSystemService(PowerManager::class.java)
            out += if (pm.isIgnoringBatteryOptimizations(ctx.packageName)) Triple("ok", "Background", "battery optimisation off")
                   else Triple("warn", "Background", "battery optimisation on — messages may stop with the screen off")
            val n = Runtime.node
            if (n == null) out += Triple("bad", "Muninn", "not running") else {
                val m = n.mesh
                out += if (m.name.isNotEmpty()) Triple("ok", "Your name", m.name) else Triple("bad", "Your name", "not set")
                val people = m.activePeers()
                if (people.isEmpty()) out += Triple("warn", "People", "you don't know anyone yet — meet them before boarding")
                people.sortedBy { m.displayName(it.nodeId).lowercase() }.forEach { p ->
                    val pr = n.presence(p.nodeId)
                    out += Triple(if (pr.state == "direct" || pr.state == "relay") "ok" else "warn", m.displayName(p.nodeId), pr.text)
                }
                out += Triple("ok", "Reconnect", n.reconnect.label)
            }
            out += Triple("ok", "Version", Updater.current + " — make sure friends run the same")
            out
        }
    }
    Text("Before the flight", style = MaterialTheme.typography.titleSmall)
    rows.forEach { (lvl, label, detail) ->
        Row(Modifier.padding(vertical = 2.dp)) {
            Text(when (lvl) { "ok" -> "✓"; "warn" -> "⚠"; else -> "✗" }, Modifier.width(22.dp),
                color = when (lvl) { "ok" -> GREEN; "warn" -> Color(0xFFF59E0B); else -> MaterialTheme.colorScheme.error })
            Column {
                Text(label, style = MaterialTheme.typography.bodyMedium)
                Text(detail, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}

// ---------------------------------------------------------------- blocked

@Composable
fun BlockedCard() {
    val tick by Runtime.tick.collectAsState()
    val blocked = remember(tick) { Runtime.node?.mesh?.blocked?.toList()?.sorted() ?: emptyList() }
    if (blocked.isEmpty()) return
    Text("Blocked", style = MaterialTheme.typography.titleSmall)
    blocked.forEach { id ->
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(id, Modifier.weight(1f), style = MaterialTheme.typography.bodySmall)
            TextButton(onClick = { Thread { Runtime.node?.mesh?.unblock(id); Runtime.bump() }.start() }) { Text("Unblock") }
        }
    }
    HorizontalDivider()
}

// ---------------------------------------------------------------- updates

@Composable
fun UpdateCard(compact: Boolean = false) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    val st by Updater.state.collectAsState()
    fun check() = scope.launch(Dispatchers.IO) { Updater.check(ctx) }
    fun install() { if (Updater.ensurePermission(ctx)) scope.launch(Dispatchers.IO) { Updater.install(ctx) } }
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        if (!compact) Text("You have Muninn ${Updater.current}. Updates come from the project's GitHub releases " +
            "when you're online; nothing is sent over Bluetooth.",
            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        when (val s = st) {
            is Updater.State.Idle -> OutlinedButton(onClick = { check() }) { Text("Check for updates") }
            is Updater.State.Checking -> Row(verticalAlignment = Alignment.CenterVertically) {
                CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
                Text("  Checking…")
            }
            is Updater.State.UpToDate -> Row(verticalAlignment = Alignment.CenterVertically) {
                Text("✓ Up to date (${s.version})", Modifier.weight(1f))
                TextButton(onClick = { check() }) { Text("Check again") }
            }
            is Updater.State.Available -> {
                Text("Muninn ${s.version} is available" + if (s.sizeBytes > 0) " (${s.sizeBytes / 1_000_000} MB)" else "",
                    fontWeight = FontWeight.Medium)
                if (s.notes.isNotBlank() && !compact) Text(s.notes.take(400), style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                Button(onClick = { install() }) { Text("Download and install") }
            }
            is Updater.State.Downloading -> {
                Text("Downloading ${s.version}… ${(s.progress * 100).toInt()}%")
                androidx.compose.material3.LinearProgressIndicator(progress = { s.progress }, modifier = Modifier.fillMaxWidth())
            }
            is Updater.State.Installing -> Text("Installing ${s.version}… Muninn restarts by itself.")
            is Updater.State.Failed -> Row(verticalAlignment = Alignment.CenterVertically) {
                Text(s.message, Modifier.weight(1f), color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall)
                TextButton(onClick = { check() }) { Text("Retry") }
            }
        }
    }
}

// ---------------------------------------------------------------- Nearby

/** "Make me visible" with feedback: whether it worked and for how long. */
@SuppressLint("MissingPermission")
@Composable
fun VisibilityCard() {
    val ctx = LocalContext.current
    val adapter = remember { ctx.getSystemService(BluetoothManager::class.java)?.adapter }
    var now by remember { mutableStateOf(System.currentTimeMillis()) }
    LaunchedEffect(Unit) { while (true) { delay(1000); now = System.currentTimeMillis() } }
    val launcher = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) { r ->
        // The system returns the granted duration in seconds, or CANCELED.
        if (r.resultCode > 0) {
            Runtime.visibleUntil = System.currentTimeMillis() + r.resultCode * 1000L
            Runtime.toast("Visible for ${r.resultCode / 60} min — other phones can find you now")
            Runtime.node?.poke()
        } else Runtime.toast("Not visible: the request was declined")
    }
    val discoverable = runCatching { adapter?.scanMode == BluetoothAdapter.SCAN_MODE_CONNECTABLE_DISCOVERABLE }.getOrDefault(false)
    val left = ((Runtime.visibleUntil - now) / 1000).coerceAtLeast(0)
    Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp)) {
        if (discoverable || left > 0) {
            Text(if (left > 0) "● Visible to nearby phones for ${left / 60}:${"%02d".format(left % 60)}" else "● Visible to nearby phones",
                color = MaterialTheme.colorScheme.primary, fontWeight = FontWeight.Medium)
            Text("Keep Muninn open on the other phone and tap ⟳ there.",
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        } else {
            Text("Phones are hidden by default. For the very first connection, one of the two must be visible.",
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            OutlinedButton(onClick = {
                runCatching {
                    launcher.launch(Intent(BluetoothAdapter.ACTION_REQUEST_DISCOVERABLE)
                        .putExtra(BluetoothAdapter.EXTRA_DISCOVERABLE_DURATION, 300))
                }.onFailure { Runtime.toast("Couldn't ask Android to make this phone visible") }
            }, modifier = Modifier.padding(top = 6.dp)) { Text("Make me visible for 5 minutes") }
        }
    }
}

/** "Tap phones": read the other phone over NFC (see TapPair.kt). */
@Composable
fun TapCard() {
    val ctx = LocalContext.current
    val activity = ctx as? android.app.Activity ?: return
    if (!TapPair.available(ctx)) return
    val st by TapPair.state.collectAsState()
    var enabled by remember { mutableStateOf(TapPair.enabled(ctx)) }
    val visible = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) { r ->
        if (r.resultCode > 0) Runtime.visibleUntil = System.currentTimeMillis() + r.resultCode * 1000L
    }
    DisposableEffect(Unit) { onDispose { TapPair.stopReading(activity); TapPair.reset() } }
    LaunchedEffect(st) {
        val s = st
        if (s is TapPair.State.Reading) { delay(60_000); TapPair.stopReading(activity) }
        // neither phone knows the other's address: let this one be found
        if (s is TapPair.State.Connecting && s.visibleNeeded) runCatching {
            visible.launch(Intent(BluetoothAdapter.ACTION_REQUEST_DISCOVERABLE)
                .putExtra(BluetoothAdapter.EXTRA_DISCOVERABLE_DURATION, 120))
        }
    }
    LaunchedEffect(Unit) { while (true) { enabled = TapPair.enabled(ctx); delay(1500) } }
    androidx.compose.material3.Surface(
        color = MaterialTheme.colorScheme.secondaryContainer,
        shape = androidx.compose.foundation.shape.RoundedCornerShape(14.dp),
        modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp),
    ) {
        Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text("📱 Tap phones to meet", fontWeight = FontWeight.SemiBold)
            val s = st
            when {
                !enabled -> {
                    Text("NFC is off.", style = MaterialTheme.typography.bodySmall)
                    OutlinedButton(onClick = {
                        runCatching { ctx.startActivity(Intent(Settings.ACTION_NFC_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
                    }) { Text("Turn on NFC") }
                }
                s is TapPair.State.Idle -> {
                    Text("The quickest way to meet someone: no searching, no visibility. Press the button, then hold the backs " +
                        "of the two phones together (theirs unlocked, Muninn installed).",
                        style = MaterialTheme.typography.bodySmall)
                    Button(onClick = { TapPair.startReading(activity) }) { Text("Tap phones") }
                }
                s is TapPair.State.Reading -> Row(verticalAlignment = Alignment.CenterVertically) {
                    CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp)
                    Text("  Hold the other phone against the back of this one…", Modifier.weight(1f),
                        style = MaterialTheme.typography.bodySmall)
                    TextButton(onClick = { TapPair.stopReading(activity) }) { Text("Cancel") }
                }
                s is TapPair.State.Connecting -> Row(verticalAlignment = Alignment.CenterVertically) {
                    CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp)
                    Text("  Met ${s.name} — connecting over Bluetooth…" +
                        if (s.visibleNeeded) " (allow visibility so their phone can find this one)" else "",
                        style = MaterialTheme.typography.bodySmall)
                }
                s is TapPair.State.Connected -> Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("✓ Connected to ${s.name}", Modifier.weight(1f), color = MaterialTheme.colorScheme.primary)
                    Button(onClick = { TapPair.reset(); Nav.back(); Nav.go(Screen.Chat(dmConv(s.nodeId))) }) { Text("Open chat") }
                }
                s is TapPair.State.Failed -> {
                    Text(s.message, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall)
                    Button(onClick = { TapPair.startReading(activity) }) { Text("Try again") }
                }
            }
        }
    }
}

private val STATE_LABEL = mapOf(
    "connected" to "connected",
    "checking" to "checking for Muninn…",
    "muninn" to "Muninn",
    "not_muninn" to "not running Muninn",
    "ignored" to "not a phone or computer — left alone",
    "unknown" to "",
)

@Composable
fun NearbyScreen() {
    val scope = rememberCoroutineScope()
    val tick by Runtime.tick.collectAsState()
    var results by remember { mutableStateOf<List<ScanResult>>(emptyList()) }
    var scanning by remember { mutableStateOf(false) }
    var scans by remember { mutableStateOf(0) }
    var showAll by remember { mutableStateOf(false) }
    var connecting by remember { mutableStateOf(setOf<String>()) }
    var now by remember { mutableStateOf(0L) }
    LaunchedEffect(Unit) { while (true) { now = System.currentTimeMillis(); delay(1000) } }

    fun scan() {
        if (scanning) return
        scanning = true
        scope.launch(Dispatchers.IO) {
            results = runCatching { Runtime.node?.scanNow(10_000) }.getOrNull() ?: emptyList()
            scans++
            scanning = false
        }
    }
    LaunchedEffect(Unit) { scan() }

    fun connect(addr: String, label: String) {
        connecting = connecting + addr
        scope.launch(Dispatchers.IO) {
            val confirmed = Runtime.node?.deviceState(addr) == "muninn" || results.firstOrNull { it.addr == addr }?.muninn == true
            val r = runCatching { Runtime.node?.connectTo(addr, probe = !confirmed) }
            connecting = connecting - addr
            r.onSuccess { id ->
                if (id != null) {
                    Runtime.toast("Connected to ${Runtime.node?.mesh?.displayName(id)}")
                    withContext(Dispatchers.Main) { Nav.back(); Nav.go(Screen.Chat(dmConv(id))) }
                } else Runtime.toast("$label answered but isn't speaking Muninn")
            }.onFailure { Runtime.toast("Couldn't reach $label — is Muninn open on it?") }
        }
    }

    val node = Runtime.node
    // State is re-read on every tick: probes finish in the background.
    val rows = remember(results, tick, now) {
        results.map { r -> Triple(r, node?.deviceState(r.addr) ?: "unknown", r.muninn == true) }
    }
    val muninnRows = rows.filter { (_, st, m) -> m || st == "muninn" || st == "connected" }
    val shown = if (showAll) rows.sortedBy { it.second !in setOf("connected", "muninn") } else muninnRows
    val checking = rows.count { it.second == "checking" }

    SimpleScaffold("People nearby", actions = {
        if (scanning) CircularProgressIndicator(Modifier.size(22.dp).padding(end = 4.dp), strokeWidth = 2.dp)
        else IconButton(onClick = { scan() }) { Icon(Icons.Default.Refresh, "Look again") }
    }) { mod ->
        Column(mod.fillMaxSize()) {
            TapCard()
            VisibilityCard()
            HorizontalDivider()
            Text(
                when {
                    scanning -> "Looking around (about 10 s)…"
                    scans == 0 -> ""
                    else -> "Saw ${rows.size} Bluetooth device${if (rows.size == 1) "" else "s"}, " +
                        "${muninnRows.size} running Muninn" + if (checking > 0) " · checking $checking phone${if (checking == 1) "" else "s"}…" else ""
                },
                style = MaterialTheme.typography.bodyMedium, modifier = Modifier.padding(horizontal = 16.dp, vertical = 10.dp),
            )
            Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp), verticalAlignment = Alignment.CenterVertically) {
                Text("Show all Bluetooth devices", Modifier.weight(1f), style = MaterialTheme.typography.bodyMedium)
                Switch(checked = showAll, onCheckedChange = { showAll = it })
            }
            HorizontalDivider(Modifier.padding(top = 8.dp))
            if (shown.isEmpty() && !scanning) Text(
                "No Muninn devices in sight yet. Phones only show up while visible — tap \"Make me visible\" on one of them, " +
                    "then ⟳ on the other. Laptops running Muninn are always visible. " +
                    "Once two devices have connected they find each other by themselves.",
                Modifier.padding(24.dp), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            LazyColumn {
                items(shown, key = { it.first.addr }) { (r, st, _) ->
                    val name = r.name.ifBlank { r.addr }
                    val isMuninn = st == "muninn" || st == "connected" || r.muninn == true
                    Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 10.dp), verticalAlignment = Alignment.CenterVertically) {
                        Avatar(name, if (st == "connected") "direct" else null, 38)
                        Spacer(Modifier.width(12.dp))
                        Column(Modifier.weight(1f)) {
                            Text(name, fontWeight = FontWeight.Medium)
                            val label = STATE_LABEL[st].orEmpty().ifEmpty { r.kind }
                            Text("$label · ${r.addr}", style = MaterialTheme.typography.labelSmall,
                                color = if (isMuninn) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                        when {
                            r.addr in connecting || st == "checking" -> CircularProgressIndicator(Modifier.size(24.dp), strokeWidth = 2.dp)
                            st == "connected" -> {}
                            isMuninn -> Button(onClick = { connect(r.addr, name) }) { Text("Connect") }
                            st != "ignored" -> OutlinedButton(onClick = { connect(r.addr, name) }) { Text("Try") }
                        }
                    }
                }
            }
        }
    }
}

// ---------------------------------------------------------------- groups

@Composable
private fun PeerChecklist(selected: Set<String>, exclude: Set<String> = emptySet(), onToggle: (String) -> Unit) {
    val tick by Runtime.tick.collectAsState()
    val peers by produceState(emptyList<Pair<String, String>>(), tick) {
        value = withContext(Dispatchers.IO) {
            val m = Runtime.node?.mesh ?: return@withContext emptyList()
            m.activePeers().map { it.nodeId to it.display }.filter { it.first !in exclude }.sortedBy { it.second.lowercase() }
        }
    }
    if (peers.isEmpty()) Text("You don't know anyone yet — connect to someone first.", Modifier.padding(16.dp))
    LazyColumn {
        items(peers, key = { it.first }) { (id, name) ->
            val reach = Runtime.node?.mesh?.reachable(id) == true
            Row(Modifier.fillMaxWidth().clickable { onToggle(id) }.padding(horizontal = 12.dp, vertical = 6.dp),
                verticalAlignment = Alignment.CenterVertically) {
                Checkbox(checked = id in selected, onCheckedChange = { onToggle(id) })
                Avatar(name, if (reach) "direct" else "offline", 34)
                Spacer(Modifier.width(10.dp))
                Text(name)
            }
        }
    }
}

@Composable
fun NewGroupScreen() {
    var name by remember { mutableStateOf("") }
    var picked by remember { mutableStateOf(setOf<String>()) }
    val scope = rememberCoroutineScope()
    SimpleScaffold("New group", actions = {
        TextButton(enabled = name.isNotBlank() && picked.isNotEmpty(), onClick = {
            scope.launch(Dispatchers.IO) {
                runCatching { Runtime.node!!.mesh.createGroup(name.trim(), picked.toList()) }
                    .onSuccess { gid -> withContext(Dispatchers.Main) { Nav.back(); Nav.go(Screen.Chat(groupConv(gid))) } }
                    .onFailure { Runtime.toast(it.message ?: "failed") }
            }
        }) { Text("Create") }
    }) { mod ->
        Column(mod.fillMaxSize()) {
            OutlinedTextField(name, { name = it }, label = { Text("Group name") }, singleLine = true,
                modifier = Modifier.fillMaxWidth().padding(16.dp))
            Text("Members (people out of range get added when they're back)", style = MaterialTheme.typography.labelMedium,
                modifier = Modifier.padding(horizontal = 16.dp))
            PeerChecklist(picked) { id -> picked = if (id in picked) picked - id else picked + id }
        }
    }
}

@Composable
fun GroupInfoScreen(conv: String) {
    val tick by Runtime.tick.collectAsState()
    val gid = convGroupId(conv) ?: return
    val scope = rememberCoroutineScope()
    val group = Runtime.node?.mesh?.let { m -> synchronized(m.lock) { m.groups[gid.hex()] } }
    val members by produceState(emptyList<String>(), tick) {
        value = withContext(Dispatchers.IO) { Runtime.node?.mesh?.let { m -> synchronized(m.lock) { group?.members?.toList() } } ?: emptyList() }
    }
    var rename by remember { mutableStateOf(group?.name ?: "") }
    var adding by remember { mutableStateOf(false) }
    var picked by remember { mutableStateOf(setOf<String>()) }
    SimpleScaffold(group?.name ?: "Group") { mod ->
        Column(mod.fillMaxSize().padding(16.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                OutlinedTextField(rename, { rename = it }, label = { Text("Name") }, singleLine = true, modifier = Modifier.weight(1f))
                TextButton(enabled = rename.isNotBlank() && rename != group?.name, onClick = {
                    scope.launch(Dispatchers.IO) { Runtime.node?.mesh?.updateGroup(gid, name = rename.trim()) }
                }) { Text("Rename") }
            }
            Spacer(Modifier.size(12.dp))
            if (adding) {
                Text("Add people", style = MaterialTheme.typography.titleSmall)
                Box(Modifier.weight(1f)) { PeerChecklist(picked, members.toSet()) { id -> picked = if (id in picked) picked - id else picked + id } }
                Row {
                    TextButton(onClick = { adding = false }) { Text("Cancel") }
                    Button(enabled = picked.isNotEmpty(), onClick = {
                        val p = picked.toList(); adding = false; picked = emptySet()
                        scope.launch(Dispatchers.IO) { Runtime.node?.mesh?.updateGroup(gid, add = p) }
                    }) { Text("Add") }
                }
            } else {
                Text("${members.size} members", style = MaterialTheme.typography.titleSmall)
                LazyColumn(Modifier.weight(1f)) {
                    items(members) { id ->
                        val m = Runtime.node?.mesh
                        val me = id == Runtime.node?.nodeId
                        val h = m?.hops(id)
                        Row(Modifier.fillMaxWidth().padding(vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
                            Avatar(m?.displayName(id) ?: id, if (me) "direct" else when { h == 1 -> "direct"; h != null -> "relay"; else -> "offline" }, 34)
                            Spacer(Modifier.width(10.dp))
                            Text((if (me) "You" else m?.displayName(id) ?: id), Modifier.weight(1f))
                            if (!me && group?.left == false) TextButton(onClick = {
                                scope.launch(Dispatchers.IO) { m?.updateGroup(gid, remove = listOf(id)) }
                            }) { Text("Remove") }
                        }
                    }
                }
                if (group?.left == false) Row {
                    OutlinedButton(onClick = { adding = true }) { Text("Add people") }
                    Spacer(Modifier.width(8.dp))
                    OutlinedButton(onClick = {
                        scope.launch(Dispatchers.IO) { Runtime.node?.mesh?.leaveGroup(gid) }
                        Nav.back()
                    }) { Text("Leave group", color = MaterialTheme.colorScheme.error) }
                }
            }
        }
    }
}

// ---------------------------------------------------------------- settings

@SuppressLint("BatteryLife")
@Composable
fun SettingsScreen() {
    val ctx = LocalContext.current
    val p = Runtime.prefs(ctx)
    val node = Runtime.node
    var name by remember { mutableStateOf(node?.mesh?.name ?: p.getString("name", "") ?: "") }
    var boot by remember { mutableStateOf(p.getBoolean("start_on_boot", true)) }
    var sim by remember { mutableStateOf(p.getString("sim_hub", "") ?: "") }
    val pm = ctx.getSystemService(PowerManager::class.java)
    val unrestricted = pm.isIgnoringBatteryOptimizations(ctx.packageName)
    SimpleScaffold("Settings") { mod ->
        Column(mod.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
            Text("Your name", style = MaterialTheme.typography.titleSmall)
            Row(verticalAlignment = Alignment.CenterVertically) {
                OutlinedTextField(name, { name = it }, singleLine = true, modifier = Modifier.weight(1f))
                TextButton(onClick = {
                    p.edit().putString("name", name.trim()).apply()
                    Thread { Runtime.node?.mesh?.setName(name.trim()) }.start()
                    Runtime.toast("Name updated")
                }) { Text("Save") }
            }
            Text("Shown to everyone you chat with, and to people who reach you through relays.",
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            HorizontalDivider()
            Text("Be visible", style = MaterialTheme.typography.titleSmall)
            VisibilityCard()
            HorizontalDivider()
            DoctorCard()
            HorizontalDivider()
            Text("Photos", style = MaterialTheme.typography.titleSmall)
            var fullPhotos by remember { mutableStateOf(p.getBoolean("photo_original", false)) }
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text("Send photos in full quality")
                    Text("Off: shrunk to 1600 px and stripped of location — seconds instead of minutes over Bluetooth.",
                        style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                Switch(checked = fullPhotos, onCheckedChange = { fullPhotos = it; p.edit().putBoolean("photo_original", it).apply() })
            }
            HorizontalDivider()
            BackupCard()
            HorizontalDivider()
            Text("Privacy", style = MaterialTheme.typography.titleSmall)
            val mesh = Runtime.node?.mesh
            var receipts by remember { mutableStateOf(mesh?.privacy("receipts") ?: true) }
            var typingOn by remember { mutableStateOf(mesh?.privacy("typing") ?: true) }
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("Send read receipts", Modifier.weight(1f))
                Switch(checked = receipts, onCheckedChange = { receipts = it; Thread { mesh?.setPrivacy("receipts", it) }.start() })
            }
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("Show when I'm typing", Modifier.weight(1f))
                Switch(checked = typingOn, onCheckedChange = { typingOn = it; Thread { mesh?.setPrivacy("typing", it) }.start() })
            }
            HorizontalDivider()
            BlockedCard()
            Text("Updates", style = MaterialTheme.typography.titleSmall)
            UpdateCard()
            HorizontalDivider()
            Text("Stay connected in the background", style = MaterialTheme.typography.titleSmall)
            Text(if (unrestricted) "✓ Battery optimisation is off for Muninn — messages keep flowing with the screen off."
                 else "Some phones kill background Bluetooth. Turn off battery optimisation so Muninn can relay and receive with the screen off.",
                style = MaterialTheme.typography.bodySmall)
            if (!unrestricted) Button(onClick = {
                runCatching {
                    ctx.startActivity(Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:${ctx.packageName}"))
                        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                }
            }) { Text("Allow background") }
            HorizontalDivider()
            Text("Reconnecting", style = MaterialTheme.typography.titleSmall)
            Text("People you've met are reconnected by address — no scanning, nothing sent to other devices. " +
                "Finding *new* people only happens for a few minutes when you open Nearby (or while you know nobody).",
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            var mode by remember { mutableStateOf(Runtime.node?.reconnect?.name ?: com.muninn.core.ScanPolicy.DEFAULT.name) }
            com.muninn.core.ScanPolicy.ALL.values.forEach { m ->
                Row(Modifier.fillMaxWidth().clickable {
                    mode = m.name
                    Thread { runCatching { Runtime.node?.setReconnectMode(m.name) } }.start()
                }.padding(vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                    androidx.compose.material3.RadioButton(selected = mode == m.name, onClick = null)
                    Spacer(Modifier.width(8.dp))
                    Column {
                        Text(m.label)
                        Text(m.description, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            }
            Text("Muninn never pairs with or prompts other people's devices; headsets and watches are never contacted.",
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("Start after reboot", Modifier.weight(1f))
                Switch(checked = boot, onCheckedChange = { boot = it; p.edit().putBoolean("start_on_boot", it).apply() })
            }
            HorizontalDivider()
            Text("Developer", style = MaterialTheme.typography.titleSmall)
            OutlinedTextField(sim, { sim = it }, label = { Text("Simulator hub (host:port, empty = Bluetooth)") }, singleLine = true,
                modifier = Modifier.fillMaxWidth())
            OutlinedButton(onClick = {
                p.edit().putString("sim_hub", sim.trim()).apply()
                ctx.stopService(Intent(ctx, MuninnService::class.java))
                Runtime.stop()
                ctx.startForegroundService(Intent(ctx, MuninnService::class.java))
                Runtime.toast("Radio restarted")
            }) { Text("Apply & restart radio") }
            Text("Your id: ${node?.nodeId ?: "…"}", style = MaterialTheme.typography.labelSmall)
            Text("Muninn — encrypted Bluetooth mesh chat. MIT licensed.", style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

// ---------------------------------------------------------------- image

@Composable
fun ImageScreen(path: String) {
    Box(Modifier.fillMaxSize().background(Color.Black).clickable { Nav.back() }, contentAlignment = Alignment.Center) {
        MediaImage(path, Modifier.fillMaxSize())
    }
}

// ---------------------------------------------------------------- game

@SuppressLint("SetJavaScriptEnabled")
@Composable
fun GameScreen(session: String) {
    val tick by Runtime.tick.collectAsState()
    val rt by Runtime.rt.collectAsState()
    val host = Runtime.games ?: return
    var web by remember { mutableStateOf<WebView?>(null) }
    var loaded by remember { mutableStateOf(false) }
    var status by remember { mutableStateOf("") }
    var over by remember { mutableStateOf(false) }
    val info by produceState(emptyMap<String, Any?>(), tick) { value = withContext(Dispatchers.IO) { host.sessionInfo(session) } }
    val code = remember(session) { host.codeFor(session) }

    LaunchedEffect(info, loaded) { if (loaded) web?.evaluateJavascript("window.muninn && window.muninn.update(${Json.write(Json.write(info))})", null) }
    LaunchedEffect(rt) {
        val r = rt ?: return@LaunchedEffect
        if (r.first == session) web?.evaluateJavascript("window.muninn.rt(${Json.write(r.second)}, ${Json.write(Json.write(r.third))})", null)
    }

    SimpleScaffold("${info["icon"] ?: "🎮"} ${info["name"] ?: "Game"}", actions = {
        if (info["started"] == true && info["ended"] != true && info["amPlayer"] == true && !over)
            TextButton(onClick = { Thread { runCatching { host.act(session, "resign", null) } }.start() }) { Text("Resign") }
        if (info["creator"] == info["me"] && info["started"] != true && info["withdrawn"] != true && info.isNotEmpty())
            TextButton(onClick = {
                Thread { runCatching { host.withdraw(session) }.onSuccess { Runtime.toast("Invite unsent") } }.start()
                Nav.back()
            }) { Text("Unsend", color = MaterialTheme.colorScheme.error) }
    }) { mod ->
        Column(mod.fillMaxSize()) {
            val names = (info["names"] as? Map<*, *>) ?: emptyMap<String, String>()
            val line = when {
                info.isEmpty() -> "Loading…"
                info["started"] != true && info["ended"] != true ->
                    "Players: " + ((info["joined"] as? List<*>) ?: emptyList<Any>()).joinToString { names[it]?.toString() ?: "?" } + " — ${info["status"]}"
                info["ended"] == true -> info["status"].toString()
                else -> status
            }
            Text(line, Modifier.padding(16.dp, 8.dp), style = MaterialTheme.typography.bodyMedium)
            val score = remember(info) { scoreLine(host.scores(info["conv"] as? String).firstOrNull { it.app == info["app"] }) }
            if (score.isNotEmpty()) Text("Score: $score", Modifier.padding(horizontal = 16.dp),
                style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            if (info["started"] != true && info["ended"] != true) Row(Modifier.padding(horizontal = 16.dp)) {
                if (info["invitedMe"] == true) {
                    Button(onClick = { Thread { host.act(session, "join", null) }.start() }) { Text("Join") }
                    Spacer(Modifier.width(8.dp))
                    OutlinedButton(onClick = { Thread { host.act(session, "decline", null) }.start(); Nav.back() }) { Text("Decline") }
                }
                val joined = (info["joined"] as? List<*>)?.size ?: 0
                if (info["creator"] == info["me"] && joined >= ((info["min"] as? Number)?.toInt() ?: 2)) {
                    Button(onClick = { Thread { host.act(session, "start", mapOf("players" to info["joined"])) }.start() }) { Text("Start now") }
                }
            }
            AndroidView(
                factory = { ctx ->
                    WebView(ctx).apply {
                        settings.javaScriptEnabled = true
                        settings.allowFileAccess = false
                        settings.allowContentAccess = false
                        setBackgroundColor(android.graphics.Color.parseColor("#0f1115"))
                        addJavascriptInterface(object {
                            @JavascriptInterface fun code(): String = code
                            @JavascriptInterface fun move(json: String) { runCatching { host.act(session, "move", Json.parse(json)) } }
                            @JavascriptInterface fun rt(json: String) { runCatching { host.rt(session, Json.parse(json)) } }
                            @JavascriptInterface fun result(json: String) { runCatching { host.act(session, "result", Json.parse(json)) } }
                            @JavascriptInterface fun toast(msg: String) = Runtime.toast(msg)
                            @JavascriptInterface fun status(json: String) {
                                val s = Json.obj(json)
                                post {
                                    over = s["over"] == true
                                    status = (s["text"] as? String ?: "") + if (!over) { if (s["spectator"] == true) "  ·  👀 watching" else if (s["myTurn"] == true) "  ·  your move" else "  ·  waiting" } else ""
                                }
                            }
                        }, "Android")
                        webChromeClient = object : android.webkit.WebChromeClient() {
                            override fun onConsoleMessage(m: android.webkit.ConsoleMessage): Boolean {
                                android.util.Log.i("MuninnGame", "${m.message()} @${m.lineNumber()}")
                                return true
                            }
                        }
                        webViewClient = object : android.webkit.WebViewClient() {
                            override fun onPageFinished(view: WebView?, url: String?) {
                                loaded = true
                                view?.let { v ->
                                    val dens = v.resources.displayMetrics.density
                                    v.evaluateJavascript("window.muninn.size(${v.width / dens}, ${v.height / dens})", null)
                                }
                            }
                        }
                        addOnLayoutChangeListener { v, l, t, r, b, _, _, _, _ ->
                            val dens = v.resources.displayMetrics.density
                            (v as WebView).evaluateJavascript(
                                "window.muninn && window.muninn.size(${(r - l) / dens}, ${(b - t) / dens})", null)
                        }
                        loadUrl("file:///android_asset/gamehost.html")
                        web = this
                    }
                },
                modifier = Modifier.fillMaxWidth().weight(1f).padding(8.dp),
            )
        }
    }
}


// ---------------------------------------------------------------- search

@Composable
fun SearchScreen() {
    var q by remember { mutableStateOf("") }
    val results by produceState(emptyList<com.muninn.core.StoredMessage>(), q) {
        value = if (q.trim().length < 2) emptyList()
        else withContext(Dispatchers.IO) { Runtime.node?.mesh?.storage?.search(q.trim(), 80) ?: emptyList() }
    }
    SimpleScaffold("Search") { mod ->
        Column(mod.fillMaxSize()) {
            OutlinedTextField(q, { q = it }, placeholder = { Text("Search all chats") }, singleLine = true,
                modifier = Modifier.fillMaxWidth().padding(16.dp))
            LazyColumn {
                items(results, key = { it.idHex }) { m ->
                    val mesh = Runtime.node?.mesh
                    val who = if (m.sender == Runtime.node?.nodeId) "You" else mesh?.displayName(m.sender) ?: ""
                    Column(Modifier.fillMaxWidth().clickable { Nav.go(Screen.Chat(m.conv)) }.padding(horizontal = 16.dp, vertical = 8.dp)) {
                        Text("${Runtime.convTitle(m.conv)} · ${timeLabel(m.ts)}", style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant)
                        Text("$who: " + m.text.ifEmpty { "📎 " + (m.meta["n"] ?: "") }, maxLines = 2)
                    }
                }
            }
        }
    }
}
