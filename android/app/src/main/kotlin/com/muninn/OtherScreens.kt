package com.muninn

import android.annotation.SuppressLint
import android.content.Intent
import android.net.Uri
import android.os.PowerManager
import android.provider.Settings
import android.webkit.JavascriptInterface
import android.webkit.WebView
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

// ---------------------------------------------------------------- Nearby

@Composable
fun NearbyScreen(discovery: BtDiscovery) {
    val scope = rememberCoroutineScope()
    val sim = Runtime.transport !is BtTransport
    val devices by discovery.devices.collectAsState()
    val scanning by discovery.scanning.collectAsState()
    var simResults by remember { mutableStateOf<List<ScanResult>>(emptyList()) }
    var simScanning by remember { mutableStateOf(false) }
    var showAll by remember { mutableStateOf(false) }
    var connecting by remember { mutableStateOf(setOf<String>()) }

    fun scan() {
        if (sim) {
            simScanning = true
            scope.launch(Dispatchers.IO) { simResults = Runtime.node?.scanNow() ?: emptyList(); simScanning = false }
        } else discovery.scan()
    }
    LaunchedEffect(Unit) { scan() }
    DisposableEffect(Unit) { onDispose { discovery.stopScan() } }

    fun connect(addr: String, label: String) {
        connecting = connecting + addr
        scope.launch(Dispatchers.IO) {
            val confirmed = discovery.devices.value.firstOrNull { it.address == addr }?.muninn == true ||
                simResults.firstOrNull { it.addr == addr }?.muninn == true
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

    val rows: List<Triple<String, String, Boolean>> =
        if (sim) simResults.map { Triple(it.addr, it.name, it.muninn == true) }
        else devices.map { Triple(it.address, it.label, it.muninn) }
    val shown = rows.filter { showAll || it.third }.sortedBy { !it.third }
    val busy = if (sim) simScanning else scanning

    SimpleScaffold("People nearby", actions = {
        if (busy) CircularProgressIndicator(Modifier.size(22.dp).padding(end = 4.dp), strokeWidth = 2.dp)
        else IconButton(onClick = { scan() }) { Icon(Icons.Default.Refresh, "Scan") }
    }) { mod ->
        Column(mod.fillMaxSize()) {
            Text(
                "Devices running Muninn show up here. Tap one to connect — no pairing needed. " +
                    "Muninn never connects to other Bluetooth devices." +
                    if (!sim) "\n\nOther phones only appear while they're discoverable: on theirs, open Muninn → Settings → Be visible." else "",
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(16.dp),
            )
            Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp), verticalAlignment = Alignment.CenterVertically) {
                Text("Show all Bluetooth devices", Modifier.weight(1f), style = MaterialTheme.typography.bodyMedium)
                Switch(checked = showAll, onCheckedChange = { showAll = it })
            }
            HorizontalDivider(Modifier.padding(top = 8.dp))
            if (shown.isEmpty()) Text(if (busy) "Scanning…" else "No Muninn devices found yet.",
                Modifier.padding(24.dp), color = MaterialTheme.colorScheme.onSurfaceVariant)
            LazyColumn {
                items(shown, key = { it.first }) { (addr, name, muninn) ->
                    Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 10.dp), verticalAlignment = Alignment.CenterVertically) {
                        Avatar(name, null, 38)
                        Spacer(Modifier.width(12.dp))
                        Column(Modifier.weight(1f)) {
                            Text(name, fontWeight = FontWeight.Medium)
                            Text(if (muninn) "Muninn · $addr" else addr, style = MaterialTheme.typography.labelSmall,
                                color = if (muninn) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                        when {
                            addr in connecting -> CircularProgressIndicator(Modifier.size(24.dp), strokeWidth = 2.dp)
                            muninn -> Button(onClick = { connect(addr, name) }) { Text("Connect") }
                            else -> OutlinedButton(onClick = { connect(addr, name) }) { Text("Try") }
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
            Text("Let nearby phones find you for the first connection (Android asks once, for a few minutes).",
                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            OutlinedButton(onClick = {
                runCatching {
                    ctx.startActivity(Intent("android.bluetooth.adapter.action.REQUEST_DISCOVERABLE")
                        .putExtra("android.bluetooth.adapter.extra.DISCOVERABLE_DURATION", 300).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                }
            }) { Text("Make me visible for 5 minutes") }
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
            Text("Finding new people", style = MaterialTheme.typography.titleSmall)
            var mode by remember { mutableStateOf(Runtime.node?.policy?.name ?: com.muninn.core.ScanPolicy.DEFAULT.name) }
            com.muninn.core.ScanPolicy.ALL.values.forEach { pol ->
                Row(Modifier.fillMaxWidth().clickable {
                    mode = pol.name
                    Thread { runCatching { Runtime.node?.setScanMode(pol.name) } }.start()
                }.padding(vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                    androidx.compose.material3.RadioButton(selected = mode == pol.name, onClick = null)
                    Spacer(Modifier.width(8.dp))
                    Column {
                        Text(pol.label)
                        Text(pol.description, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            }
            Text("Muninn never pairs with or prompts other people's devices; headsets and watches are never contacted. " +
                "Looking around briefly slows Bluetooth audio — choose Quiet if that bothers you.",
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
                            @JavascriptInterface fun toast(msg: String) = Runtime.toast(msg)
                            @JavascriptInterface fun status(json: String) {
                                val s = Json.obj(json)
                                post {
                                    over = s["over"] == true
                                    status = (s["text"] as? String ?: "") + if (!over) { if (s["myTurn"] == true) "  ·  your move" else "  ·  waiting" } else ""
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
