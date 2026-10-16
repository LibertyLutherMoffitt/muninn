package com.muninn

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
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExtendedFloatingActionButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

val GREEN = Color(0xFF34C759)
val AMBER = Color(0xFFF5A524)

@Composable
fun Avatar(name: String, status: String?, size: Int = 44) {
    val hue = (name.hashCode() and 0xffff) % 360
    Box(Modifier.size(size.dp)) {
        Box(
            Modifier.size(size.dp).clip(CircleShape)
                .background(Color.hsl(hue.toFloat(), 0.45f, 0.42f)),
            contentAlignment = Alignment.Center,
        ) {
            Text(name.firstOrNull()?.uppercase() ?: "?", color = Color.White, fontWeight = FontWeight.Bold, fontSize = (size / 2.4).sp)
        }
        if (status != null && status != "group") {
            Box(
                Modifier.align(Alignment.BottomEnd).size(13.dp).clip(CircleShape)
                    .background(MaterialTheme.colorScheme.surface).padding(2.dp).clip(CircleShape)
                    .background(when (status) {
                        "direct" -> GREEN; "relay" -> AMBER
                        "nearby" -> Color(0xFF7FA7D9)
                        else -> MaterialTheme.colorScheme.outline
                    }),
            )
        }
    }
}

fun timeLabel(ts: Long): String {
    if (ts <= 0) return ""
    val d = Date(ts * 1000)
    val now = System.currentTimeMillis() / 1000
    return if (now - ts < 20 * 3600) SimpleDateFormat("HH:mm", Locale.getDefault()).format(d)
    else SimpleDateFormat("d MMM", Locale.getDefault()).format(d)
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ConversationsScreen() {
    val tick by Runtime.tick.collectAsState()
    val items by produceState(emptyList<Runtime.ConvItem>(), tick) {
        value = withContext(Dispatchers.IO) { Runtime.conversations() }
    }
    val status by produceState("", tick) {
        value = withContext(Dispatchers.IO) {
            val n = Runtime.node ?: return@withContext "starting…"
            val m = n.mesh
            val direct = m.connected().size
            val reach = synchronized(m.lock) { m.peers.keys.toList() }.count { m.reachable(it) }
            val bt = (Runtime.transport as? BtTransport)?.isReady ?: true
            when {
                !bt -> "Bluetooth is off"
                direct == 0 -> "Looking for people nearby…"
                else -> "$direct connected" + if (reach > direct) " · ${reach - direct} via relay" else ""
            }
        }
    }
    var askName by remember { mutableStateOf(false) }
    LaunchedEffect(tick) { if (Runtime.node?.mesh?.name?.isEmpty() == true) askName = true }
    if (askName) WelcomeDialog { askName = false }
    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text("Muninn", fontWeight = FontWeight.SemiBold)
                        Text(status, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                },
                actions = {
                    IconButton(onClick = { Nav.go(Screen.Search) }) { Text("🔍") }
                    TextButton(onClick = { Nav.go(Screen.Nearby) }) { Text("Nearby") }
                    IconButton(onClick = { Nav.go(Screen.Settings) }) { Icon(Icons.Default.Settings, "Settings") }
                },
            )
        },
        floatingActionButton = {
            ExtendedFloatingActionButton(onClick = { Nav.go(Screen.NewGroup) }, icon = { Icon(Icons.Default.Add, null) },
                text = { Text("New group") })
        },
    ) { pad ->
        if (items.isEmpty()) {
            Column(
                Modifier.fillMaxSize().padding(pad).padding(32.dp),
                verticalArrangement = Arrangement.Center,
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                Text("✈️", fontSize = 48.sp)
                Spacer(Modifier.size(12.dp))
                Text("Nobody here yet", style = MaterialTheme.typography.titleMedium)
                Spacer(Modifier.size(6.dp))
                Text(
                    "Tap Nearby to find people running Muninn. Once you've connected once, " +
                        "Muninn reconnects automatically — even through other people's phones.",
                    textAlign = TextAlign.Center, color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Spacer(Modifier.size(16.dp))
                TextButton(onClick = { Nav.go(Screen.Nearby) }) { Text("Find people nearby") }
            }
        } else {
            LazyColumn(Modifier.fillMaxSize().padding(pad)) {
                item(key = "update") {
                    val up by Updater.state.collectAsState()
                    if (up is Updater.State.Available || up is Updater.State.Downloading || up is Updater.State.Installing)
                        androidx.compose.material3.Surface(color = MaterialTheme.colorScheme.primaryContainer,
                            modifier = Modifier.fillMaxWidth().padding(12.dp, 6.dp),
                            shape = androidx.compose.foundation.shape.RoundedCornerShape(12.dp)) {
                            Column(Modifier.padding(12.dp)) { UpdateCard(compact = true) }
                        }
                }
                items(items, key = { it.conv }) { c -> ConvRow(c) }
            }
        }
    }
}

@Composable
private fun ConvRow(c: Runtime.ConvItem) {
    Row(
        Modifier.fillMaxWidth().clickable { Nav.go(Screen.Chat(c.conv)) }.padding(horizontal = 16.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Avatar(c.title, if (c.isGroup) "group" else c.status)
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    (if (c.isGroup) "👥 " else "") + c.title,
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = if (c.unread > 0) FontWeight.Bold else FontWeight.Medium,
                    maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f),
                )
                Text(timeLabel(c.ts), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    when {
                        c.typing -> "typing…"
                        c.preview.isNotEmpty() -> c.preview
                        else -> c.subtitle
                    },
                    style = MaterialTheme.typography.bodySmall,
                    color = if (c.typing) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f),
                )
                if (c.unread > 0) {
                    Box(
                        Modifier.widthIn(min = 20.dp).clip(CircleShape).background(MaterialTheme.colorScheme.primary)
                            .padding(horizontal = 6.dp, vertical = 1.dp),
                        contentAlignment = Alignment.Center,
                    ) {
                        Text(if (c.unread > 99) "99+" else c.unread.toString(), color = MaterialTheme.colorScheme.onPrimary,
                            style = MaterialTheme.typography.labelSmall)
                    }
                } else if (c.pending > 0) {
                    Text("◌ ${c.pending}", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.outline)
                }
            }
        }
    }
}


@Composable
fun WelcomeDialog(onDone: () -> Unit) {
    val ctx = androidx.compose.ui.platform.LocalContext.current
    val suggestion = remember {
        android.provider.Settings.Global.getString(ctx.contentResolver, "device_name") ?: android.os.Build.MODEL
    }
    var name by remember { mutableStateOf(suggestion) }
    androidx.compose.material3.AlertDialog(
        onDismissRequest = {},
        title = { Text("Welcome to Muninn") },
        text = {
            Column {
                Text("Encrypted chat over Bluetooth — no internet needed. People nearby show up by " +
                    "themselves, and messages hop through others to reach people further away.\n\nWhat should people call you?")
                Spacer(Modifier.size(8.dp))
                androidx.compose.material3.OutlinedTextField(name, { name = it }, singleLine = true)
            }
        },
        confirmButton = {
            TextButton(enabled = name.isNotBlank(), onClick = {
                val n = name.trim()
                Runtime.prefs(ctx).edit().putString("name", n).apply()
                Thread { Runtime.node?.mesh?.setName(n) }.start()
                onDone()
            }) { Text("Start") }
        },
    )
}
