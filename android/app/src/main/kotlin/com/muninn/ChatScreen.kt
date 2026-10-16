package com.muninn

import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.spring
import androidx.compose.foundation.gestures.detectHorizontalDragGestures
import androidx.compose.foundation.layout.offset
import com.muninn.core.hex
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.unit.IntOffset
import kotlin.math.roundToInt
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material3.Button
import androidx.compose.material3.FilterChip
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.ime
import androidx.compose.foundation.layout.navigationBars
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.union
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilledIconButton
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextField
import androidx.compose.material3.TextFieldDefaults
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.FileProvider
import com.muninn.core.StoredMessage
import com.muninn.core.convGroupId
import com.muninn.core.unhex
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

private fun dayLabel(ts: Long): String {
    val f = SimpleDateFormat("yyyyMMdd", Locale.US)
    val d = f.format(Date(ts * 1000))
    val today = f.format(Date())
    val yesterday = f.format(Date(System.currentTimeMillis() - 86_400_000))
    return when (d) { today -> "Today"; yesterday -> "Yesterday"; else -> SimpleDateFormat("EEE d MMM", Locale.getDefault()).format(Date(ts * 1000)) }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ChatScreen(conv: String) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    val tick by Runtime.tick.collectAsState()
    val node = Runtime.node
    val me = node?.nodeId ?: ""
    val isGroup = conv.startsWith("group:")
    val msgs by produceState(emptyList<StoredMessage>(), tick, conv) {
        value = withContext(Dispatchers.IO) { Runtime.node?.mesh?.storage?.loadHistory(conv, 400) ?: emptyList() }
    }
    val title by produceState("", tick) { value = withContext(Dispatchers.IO) { Runtime.convTitle(conv) } }
    val subtitle by produceState("", tick) { value = withContext(Dispatchers.IO) { Runtime.convSubtitle(conv) } }

    var draft by remember(conv) { mutableStateOf("") }
    var replyTo by remember(conv) { mutableStateOf<StoredMessage?>(null) }
    // swiping / tapping "reply" or "edit" puts the cursor in the composer
    val composerFocus = remember { FocusRequester() }
    var editing by remember(conv) { mutableStateOf<StoredMessage?>(null) }
    LaunchedEffect(replyTo, editing) {
        if (replyTo != null || editing != null) runCatching { composerFocus.requestFocus() }
    }
    var selected by remember { mutableStateOf<StoredMessage?>(null) }
    var showStickers by remember { mutableStateOf(false) }
    var showGames by remember { mutableStateOf(false) }
    var renaming by remember { mutableStateOf(false) }
    var attachMenu by remember { mutableStateOf(false) }
    var lastTyping by remember { mutableLongStateOf(0L) }
    val listState = rememberLazyListState()

    DisposableEffect(conv) {
        Runtime.visibleConv = conv
        onDispose { if (Runtime.visibleConv == conv) Runtime.visibleConv = null }
    }
    LaunchedEffect(msgs.size) {
        withContext(Dispatchers.IO) { Runtime.node?.mesh?.markRead(conv) }
        Notifications.cancel(ctx, conv)
        if (msgs.isNotEmpty()) listState.animateScrollToItem(msgs.size - 1)
    }

    fun sendPicked(uri: Uri?) {
        uri ?: return
        scope.launch(Dispatchers.IO) {
            try {
                val p = Media.prepare(ctx, uri)
                Runtime.node?.mesh?.sendFile(conv, p.file, p.mime, p.name, thumb = p.thumb, replyTo = replyTo?.msgId)
                replyTo = null
            } catch (e: Throwable) { Runtime.toast("Couldn't send: ${e.message}") }
        }
    }
    val pickMedia = rememberLauncherForActivityResult(ActivityResultContracts.PickVisualMedia()) { sendPicked(it) }
    val pickFile = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { sendPicked(it) }

    fun send() {
        val text = Emoji.expand(draft.trim())
        if (text.isEmpty()) return
        val ed = editing; val rp = replyTo
        draft = ""; editing = null; replyTo = null
        scope.launch(Dispatchers.IO) {
            try {
                val mesh = Runtime.node?.mesh ?: return@launch
                if (ed != null) mesh.edit(conv, ed.msgId, text) else mesh.sendText(conv, text, rp?.msgId)
                if (mesh.convMembers(conv).none { mesh.reachable(it) })
                    Runtime.toast("Out of range — will send automatically when they're back")
            } catch (e: Throwable) { Runtime.toast(e.message ?: "send failed") }
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                navigationIcon = { IconButton(onClick = { Nav.back() }) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back") } },
                title = {
                    Row(verticalAlignment = Alignment.CenterVertically,
                        modifier = Modifier.clickable(enabled = isGroup) { Nav.go(Screen.GroupInfo(conv)) }) {
                        Avatar(title, null, 36)
                        Spacer(Modifier.width(10.dp))
                        Column {
                            Text(title, maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.titleMedium)
                            Text(subtitle, maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.labelSmall,
                                color = if (subtitle.endsWith("typing…")) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }
                },
                actions = {
                    IconButton(onClick = { showGames = true }) { Text("🎮", fontSize = 20.sp) }
                    if (!isGroup) IconButton(onClick = { renaming = true }) { Text("✎", fontSize = 18.sp) }
                    val muted = remember(tick) { Runtime.node?.mesh?.isMuted(conv) == true }
                    IconButton(onClick = { Thread { Runtime.node?.mesh?.setMuted(conv, !muted) }.start() }) {
                        Text(if (muted) "🔕" else "🔔", fontSize = 18.sp)
                    }
                },
            )
        },
        bottomBar = {
            Column(Modifier.windowInsetsPadding(WindowInsets.navigationBars.union(WindowInsets.ime))) {
                val banner = editing?.let { "Editing message" } ?: replyTo?.let { r ->
                    "Replying to ${if (r.sender == me) "yourself" else Runtime.node?.mesh?.displayName(r.sender)}: " +
                        r.text.ifEmpty { (r.meta["n"] as? String) ?: "" }
                }
                if (banner != null) {
                    Row(Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.surfaceVariant).padding(horizontal = 12.dp, vertical = 6.dp),
                        verticalAlignment = Alignment.CenterVertically) {
                        Box(Modifier.width(3.dp).size(3.dp, 28.dp).background(MaterialTheme.colorScheme.primary))
                        Spacer(Modifier.width(8.dp))
                        Text(banner, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f),
                            style = MaterialTheme.typography.bodySmall)
                        IconButton(onClick = { replyTo = null; editing = null; if (editing != null) draft = "" }) { Icon(Icons.Default.Close, "Cancel") }
                    }
                }
                Row(Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 6.dp), verticalAlignment = Alignment.Bottom) {
                    Box {
                        IconButton(onClick = { attachMenu = true }) { Text("📎", fontSize = 20.sp) }
                        DropdownMenu(expanded = attachMenu, onDismissRequest = { attachMenu = false }) {
                            DropdownMenuItem(text = { Text("Photo / GIF") }, onClick = {
                                attachMenu = false
                                pickMedia.launch(PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly))
                            })
                            DropdownMenuItem(text = { Text("File") }, onClick = { attachMenu = false; pickFile.launch(arrayOf("*/*")) })
                            DropdownMenuItem(text = { Text("Sticker / GIF (offline)") }, onClick = { attachMenu = false; showStickers = true })
                        }
                    }
                    TextField(
                        value = draft,
                        onValueChange = {
                            draft = it
                            val now = System.currentTimeMillis()
                            if (it.isNotEmpty() && now - lastTyping > 3000) {
                                lastTyping = now
                                scope.launch(Dispatchers.IO) { runCatching { Runtime.node?.mesh?.sendTyping(conv) } }
                            }
                        },
                        placeholder = { Text("Message  ·  :shortcodes: work") },
                        modifier = Modifier.weight(1f).heightIn(min = 48.dp, max = 160.dp).focusRequester(composerFocus),
                        shape = RoundedCornerShape(24.dp),
                        colors = TextFieldDefaults.colors(
                            focusedIndicatorColor = Color.Transparent, unfocusedIndicatorColor = Color.Transparent,
                        ),
                    )
                    Spacer(Modifier.width(6.dp))
                    FilledIconButton(onClick = { send() }, enabled = draft.isNotBlank(), modifier = Modifier.size(48.dp)) {
                        Icon(Icons.AutoMirrored.Filled.Send, "Send")
                    }
                }
            }
        },
    ) { pad ->
        LazyColumn(
            state = listState,
            modifier = Modifier.fillMaxSize().padding(pad),
            contentPadding = PaddingValues(horizontal = 10.dp, vertical = 8.dp),
            verticalArrangement = Arrangement.spacedBy(3.dp),
        ) {
            items(msgs.size, key = { msgs[it].idHex }) { i ->
                val m = msgs[i]
                val prev = msgs.getOrNull(i - 1)
                if (prev == null || dayLabel(prev.ts) != dayLabel(m.ts)) {
                    Box(Modifier.fillMaxWidth().padding(vertical = 6.dp), contentAlignment = Alignment.Center) {
                        Text(dayLabel(m.ts), style = MaterialTheme.typography.labelSmall,
                            modifier = Modifier.clip(RoundedCornerShape(10.dp)).background(MaterialTheme.colorScheme.surfaceVariant)
                                .padding(horizontal = 10.dp, vertical = 3.dp))
                    }
                }
                if (m.kind == "system") {
                    Text(m.text, style = MaterialTheme.typography.labelSmall, fontStyle = FontStyle.Italic,
                        color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp),
                        textAlign = androidx.compose.ui.text.style.TextAlign.Center)
                } else {
                    val showSender = isGroup && m.sender != me && (prev == null || prev.sender != m.sender || prev.kind == "system")
                    Bubble(m, me, showSender, msgs, onLong = { selected = m },
                        onReply = { replyTo = m; editing = null })
                }
            }
        }
    }

    selected?.let { m ->
        ModalBottomSheet(onDismissRequest = { selected = null }) {
            MessageActions(m, me, conv, onReply = { replyTo = m; editing = null; selected = null },
                onEdit = { editing = m; replyTo = null; draft = m.text; selected = null }, onDone = { selected = null })
        }
    }
    if (showStickers) {
        ModalBottomSheet(onDismissRequest = { showStickers = false }) {
            StickerSheet { f ->
                showStickers = false
                scope.launch(Dispatchers.IO) {
                    runCatching { Runtime.node?.mesh?.sendFile(conv, f, "image/gif", f.name, thumb = Media.thumbnail(f)) }
                        .onFailure { Runtime.toast(it.message ?: "send failed") }
                }
            }
        }
    }
    if (renaming) {
        var nick by remember { mutableStateOf(Runtime.node?.mesh?.peers?.get(conv.removePrefix("dm:"))?.override ?: "") }
        androidx.compose.material3.AlertDialog(
            onDismissRequest = { renaming = false },
            title = { Text("Nickname for this person") },
            text = {
                Column {
                    Text("Only you see this. Leave empty to use the name they chose.",
                        style = MaterialTheme.typography.bodySmall)
                    androidx.compose.material3.OutlinedTextField(nick, { nick = it }, singleLine = true)
                }
            },
            confirmButton = {
                androidx.compose.material3.TextButton(onClick = {
                    renaming = false
                    val n = nick.trim()
                    scope.launch(Dispatchers.IO) { Runtime.node?.mesh?.setOverride(conv.removePrefix("dm:"), n.ifEmpty { null }) }
                }) { Text("Save") }
            },
            dismissButton = { androidx.compose.material3.TextButton(onClick = { renaming = false }) { Text("Cancel") } },
        )
    }
    if (showGames) {
        ModalBottomSheet(onDismissRequest = { showGames = false }) { GamesSheet(conv) { showGames = false } }
    }
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun Bubble(m: StoredMessage, me: String, showSender: Boolean, all: List<StoredMessage>,
                   onLong: () -> Unit, onReply: () -> Unit) {
    val ctx = LocalContext.current
    val out = m.sender == me
    val mesh = Runtime.node?.mesh
    // Swipe right to reply: the bubble follows the finger (with resistance),
    // a reply arrow fades in, a tick of haptics at the threshold.
    val scope = rememberCoroutineScope()
    val haptic = LocalHapticFeedback.current
    val density = LocalDensity.current
    val threshold = with(density) { 56.dp.toPx() }
    val maxPull = with(density) { 84.dp.toPx() }
    val dx = remember { Animatable(0f) }
    var armed by remember { mutableStateOf(false) }
    Box(Modifier.fillMaxWidth().pointerInput(m.msgId.hex()) {
        if (m.deleted || m.kind == "system") return@pointerInput
        detectHorizontalDragGestures(
            onDragEnd = {
                if (armed) onReply()
                armed = false
                scope.launch { dx.animateTo(0f, spring(dampingRatio = 0.6f)) }
            },
            onDragCancel = { armed = false; scope.launch { dx.animateTo(0f) } },
        ) { change, amount ->
            val next = (dx.value + amount * 0.6f).coerceIn(0f, maxPull)
            if (next != dx.value) change.consume()
            scope.launch { dx.snapTo(next) }
            val now = next >= threshold
            if (now && !armed) haptic.performHapticFeedback(HapticFeedbackType.LongPress)
            armed = now
        }
    }) {
    Text("↩", color = MaterialTheme.colorScheme.primary, style = MaterialTheme.typography.titleLarge,
        modifier = Modifier.align(Alignment.CenterStart).padding(start = 8.dp)
            .graphicsLayer { alpha = (dx.value / threshold).coerceIn(0f, 1f); scaleX = alpha; scaleY = alpha })
    Column(Modifier.fillMaxWidth().offset { IntOffset(dx.value.roundToInt(), 0) },
        horizontalAlignment = if (out) Alignment.End else Alignment.Start) {
        Surface(
            shape = RoundedCornerShape(16.dp, 16.dp, if (out) 4.dp else 16.dp, if (out) 16.dp else 4.dp),
            color = if (out) MaterialTheme.colorScheme.primaryContainer else MaterialTheme.colorScheme.surfaceVariant,
            modifier = Modifier.widthIn(max = 300.dp).combinedClickable(onClick = {
                if (m.kind == "file") openFile(ctx, m) }, onLongClick = onLong),
        ) {
            Column(Modifier.padding(horizontal = 12.dp, vertical = 8.dp)) {
                if (showSender) Text(mesh?.displayName(m.sender) ?: m.sender, color = MaterialTheme.colorScheme.primary,
                    style = MaterialTheme.typography.labelMedium, fontWeight = FontWeight.Bold)
                m.replyTo?.let { rid ->
                    val orig = all.firstOrNull { it.msgId.contentEquals(rid) }
                    Row(Modifier.padding(vertical = 4.dp).clip(RoundedCornerShape(6.dp)).background(Color(0x22000000)).padding(6.dp)) {
                        Box(Modifier.size(3.dp, 30.dp).background(MaterialTheme.colorScheme.primary))
                        Spacer(Modifier.width(6.dp))
                        Column {
                            Text(orig?.let { if (it.sender == me) "You" else mesh?.displayName(it.sender) } ?: "",
                                style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.primary)
                            Text(orig?.let { if (it.deleted) "message deleted" else it.text.ifEmpty { "📎 " + (it.meta["n"] ?: "file") } }
                                ?: "message not available", maxLines = 2, overflow = TextOverflow.Ellipsis,
                                style = MaterialTheme.typography.bodySmall)
                        }
                    }
                }
                if (m.kind == "file" && !m.deleted) FileContent(m)
                val text = if (m.deleted) "message deleted" else m.text
                if (text.isNotEmpty()) Text(text, style = MaterialTheme.typography.bodyMedium,
                    fontStyle = if (m.deleted) FontStyle.Italic else FontStyle.Normal)
                Row(Modifier.align(Alignment.End), verticalAlignment = Alignment.CenterVertically) {
                    if (m.edited && !m.deleted) Text("edited ", style = MaterialTheme.typography.labelSmall, fontStyle = FontStyle.Italic,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Text(timeLabel(m.ts), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    if (out) Text(when (m.state) { "read" -> "  ✓✓"; "delivered" -> "  ✓"; else -> "  ◌" },
                        style = MaterialTheme.typography.labelSmall,
                        color = if (m.state == "read") GREEN else MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        }
        if (m.reactions.isNotEmpty()) {
            Row(Modifier.padding(horizontal = 6.dp)) {
                m.reactions.forEach { (e, who) ->
                    val mine = me in who
                    Text(e + if (who.size > 1) " ${who.size}" else "",
                        modifier = Modifier.padding(end = 4.dp).clip(RoundedCornerShape(12.dp))
                            .background(if (mine) MaterialTheme.colorScheme.primaryContainer else MaterialTheme.colorScheme.surfaceVariant)
                            .clickable { Thread { mesh?.react(m.conv, m.msgId, e, remove = mine) }.start() }
                            .padding(horizontal = 8.dp, vertical = 2.dp),
                        style = MaterialTheme.typography.labelMedium)
                }
            }
        }
    }
    }
}

@Composable
private fun FileContent(m: StoredMessage) {
    val mesh = Runtime.node?.mesh ?: return
    val fid = (m.meta["f"] as? String) ?: return
    val rec = synchronized(mesh.lock) { mesh.files[fid] }
    val (have, total, state) = mesh.fileProgress(fid.unhex())
    val mime = (m.meta["mt"] as? String) ?: ""
    val name = (m.meta["n"] as? String) ?: "file"
    val size = (m.meta["s"] as? Number)?.toLong() ?: 0
    if (mime.startsWith("image/")) {
        val path = if (state == "complete") rec?.path else m.meta["thumb"] as? String
        Box(Modifier.width(220.dp).heightIn(min = 80.dp, max = 300.dp).clip(RoundedCornerShape(10.dp))) {
            if (path != null) MediaImage(path, Modifier.width(220.dp))
            if (state != "complete") {
                Box(Modifier.matchParentSize().background(Color(0x66000000)), contentAlignment = Alignment.Center) {
                    Text(if (state == "downloading") "${if (total > 0) have * 100 / total else 0}%" else "⬇ ${human(size)}",
                        color = Color.White)
                }
            }
        }
    } else {
        Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(vertical = 4.dp)) {
            Text(if (name.endsWith(".js")) "🎮" else "📄", fontSize = 26.sp)
            Spacer(Modifier.width(8.dp))
            Column(Modifier.widthIn(max = 200.dp)) {
                Text(name, maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.bodyMedium)
                Text(human(size) + " · " + when (state) {
                    "complete" -> if (name.endsWith(".js")) "long-press → install game" else "tap to open"
                    "downloading" -> "downloading"
                    else -> "tap to download"
                }, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        if (state == "downloading" && total > 0) LinearProgressIndicator(progress = { have.toFloat() / total }, modifier = Modifier.width(200.dp))
    }
}

fun human(n: Long): String = when {
    n < 1024 -> "$n B"
    n < 1024 * 1024 -> "%.1f KB".format(n / 1024.0)
    else -> "%.1f MB".format(n / 1024.0 / 1024.0)
}

private fun openFile(ctx: Context, m: StoredMessage) {
    val mesh = Runtime.node?.mesh ?: return
    val fid = (m.meta["f"] as? String) ?: return
    val rec = synchronized(mesh.lock) { mesh.files[fid] } ?: return
    if (rec.state != "complete") { Thread { mesh.download(fid.unhex()) }.start(); Runtime.toast("downloading…"); return }
    if (rec.mime.startsWith("image/")) { Nav.go(Screen.Image(rec.path)); return }
    val uri = FileProvider.getUriForFile(ctx, "com.muninn.files", File(rec.path))
    runCatching {
        ctx.startActivity(Intent(Intent.ACTION_VIEW).setDataAndType(uri, rec.mime.ifEmpty { "*/*" })
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION))
    }.onFailure { Runtime.toast("No app can open this file") }
}

@Composable
private fun MessageActions(m: StoredMessage, me: String, conv: String, onReply: () -> Unit, onEdit: () -> Unit, onDone: () -> Unit) {
    val ctx = LocalContext.current
    val mesh = Runtime.node?.mesh ?: return
    Column(Modifier.padding(bottom = 24.dp)) {
        Row(Modifier.fillMaxWidth().padding(12.dp), horizontalArrangement = Arrangement.SpaceEvenly) {
            Emoji.quick.forEach { e ->
                Text(e, fontSize = 26.sp, modifier = Modifier.clip(CircleShape).clickable {
                    val mine = me in (m.reactions[e] ?: emptyList())
                    Thread { mesh.react(conv, m.msgId, e, remove = mine) }.start(); onDone()
                }.padding(6.dp))
            }
        }
        HorizontalDivider()
        @Composable
        fun item(label: String, action: () -> Unit) =
            Text(label, Modifier.fillMaxWidth().clickable { action() }.padding(horizontal = 20.dp, vertical = 14.dp))
        if (!m.deleted) item("Reply") { onReply() }
        if (m.text.isNotEmpty()) item("Copy text") {
            ctx.getSystemService(ClipboardManager::class.java).setPrimaryClip(ClipData.newPlainText("message", m.text))
            Runtime.toast("Copied"); onDone()
        }
        if (m.sender == me && m.kind == "text" && !m.deleted) item("Edit") { onEdit() }
        if (m.sender == me && !m.deleted) item("Delete for everyone") { Thread { mesh.delete(conv, m.msgId) }.start(); onDone() }
        if (m.kind == "file") {
            val fid = m.meta["f"] as? String
            val rec = fid?.let { synchronized(mesh.lock) { mesh.files[it] } }
            if (rec?.state == "complete" && rec.mime.startsWith("image/")) item("Save to my stickers") {
                val dir = File(ctx.filesDir, "stickers").apply { mkdirs() }
                File(rec.path).copyTo(File(dir, File(rec.path).name), overwrite = true)
                Runtime.toast("Saved to your stickers"); onDone()
            }
            if (rec?.state == "complete" && rec.name.endsWith(".js")) item("Install game") {
                runCatching { Runtime.games?.install(File(rec.path)) }
                    .onSuccess { Runtime.toast("Installed “$it”") }.onFailure { Runtime.toast(it.message ?: "not a game") }
                onDone()
            }
            if (rec != null && rec.state != "complete") item("Download") { Thread { mesh.download(rec.fileId) }.start(); onDone() }
        }
    }
}

@Composable
private fun StickerSheet(onPick: (File) -> Unit) {
    val ctx = LocalContext.current
    val files by produceState(emptyList<File>()) {
        value = withContext(Dispatchers.IO) {
            val saved = File(ctx.filesDir, "stickers").apply { mkdirs() }.listFiles()?.sortedBy { it.name } ?: emptyList()
            val outDir = File(ctx.cacheDir, "stickers").apply { mkdirs() }
            val pack = (ctx.assets.list("stickers") ?: emptyArray()).sorted().map { name ->
                File(outDir, name).also { f -> if (!f.exists()) ctx.assets.open("stickers/$name").use { i -> f.outputStream().use { i.copyTo(it) } } }
            }
            saved + pack
        }
    }
    Column(Modifier.padding(horizontal = 12.dp)) {
        Text("Stickers & GIFs — work offline", style = MaterialTheme.typography.titleSmall, modifier = Modifier.padding(8.dp))
        LazyVerticalGrid(GridCells.Adaptive(88.dp), Modifier.heightIn(max = 420.dp)) {
            items(files) { f ->
                Box(Modifier.size(88.dp).padding(4.dp).clip(RoundedCornerShape(10.dp)).clickable { onPick(f) }) {
                    MediaImage(f.path, Modifier.fillMaxSize())
                }
            }
        }
        Spacer(Modifier.size(24.dp))
    }
}

@Composable
private fun GamesSheet(conv: String, onDone: () -> Unit) {
    val tick by Runtime.tick.collectAsState()
    val catalog by produceState(emptyList<com.muninn.core.GameHost.Meta>()) {
        value = withContext(Dispatchers.IO) { Runtime.games?.catalog() ?: emptyList() }
    }
    val sessions by produceState(emptyList<Map<String, Any?>>(), tick) {
        value = withContext(Dispatchers.IO) { Runtime.games?.sessions(conv) ?: emptyList() }
    }
    val scope = rememberCoroutineScope()
    Column(Modifier.padding(bottom = 24.dp)) {
        if (sessions.isNotEmpty()) {
            Text("Games here", style = MaterialTheme.typography.titleSmall, modifier = Modifier.padding(16.dp, 8.dp))
            sessions.forEach { s ->
                Row(Modifier.fillMaxWidth().clickable { onDone(); Nav.go(Screen.Game(s["session"] as String)) }
                    .padding(horizontal = 20.dp, vertical = 10.dp)) {
                    Text("${s["icon"]}  ${s["name"]}", modifier = Modifier.weight(1f))
                    Text((if (s["invitedMe"] == true) "invites you · " else "") + s["status"],
                        style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            HorizontalDivider()
        }
        Text("Start a game", style = MaterialTheme.typography.titleSmall, modifier = Modifier.padding(16.dp, 8.dp))
        var picking by remember { mutableStateOf<String?>(null) }   // game whose options are open
        val chosen = remember { mutableStateMapOf<String, Any?>() }
        fun invite(g: com.muninn.core.GameHost.Meta) {
            val opts = g.options.associate { o -> o.id to (chosen[o.id] ?: o.default) }
            scope.launch(Dispatchers.IO) {
                runCatching { Runtime.games!!.invite(conv, g.id, opts) }
                    .onSuccess { sid -> withContext(Dispatchers.Main) { onDone(); Nav.go(Screen.Game(sid)) } }
                    .onFailure { Runtime.toast(it.message ?: "can't start") }
            }
        }
        catalog.forEach { g ->
            Row(Modifier.fillMaxWidth().clickable {
                if (g.options.isEmpty()) invite(g)
                else { picking = if (picking == g.id) null else g.id; chosen.clear() }
            }.padding(horizontal = 20.dp, vertical = 12.dp)) {
                Text("${g.icon}  ${g.name}", modifier = Modifier.weight(1f))
                Text(if (g.min == g.max) "${g.min} players" else "${g.min}–${g.max} players",
                    style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            if (picking == g.id) Column(Modifier.padding(start = 20.dp, end = 20.dp, bottom = 8.dp)) {
                g.options.forEach { o ->
                    Text(o.label, style = MaterialTheme.typography.labelMedium)
                    Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        o.choices.forEach { c ->
                            FilterChip(selected = (chosen[o.id] ?: o.default) == c, onClick = { chosen[o.id] = c },
                                label = { Text(c.toString()) })
                        }
                    }
                }
                Button(onClick = { invite(g) }, modifier = Modifier.padding(top = 4.dp)) { Text("Invite") }
            }
        }
        if (catalog.isEmpty()) Text("No games installed", Modifier.padding(20.dp))
        HorizontalDivider()
        Text("Share a game's code (they can install it)", style = MaterialTheme.typography.titleSmall,
            modifier = Modifier.padding(16.dp, 8.dp))
        val ctx = LocalContext.current
        catalog.forEach { g ->
            Text("${g.icon}  ${g.id}.js", modifier = Modifier.fillMaxWidth().clickable {
                scope.launch(Dispatchers.IO) {
                    runCatching {
                        val src = Runtime.games!!.sources()[g.id]!!
                        val f = File(ctx.cacheDir, "${g.id}.js").apply { writeText(src) }
                        Runtime.node!!.mesh.sendFile(conv, f, "application/x-muninn-game", f.name)
                    }.onSuccess { withContext(Dispatchers.Main) { onDone() } }
                        .onFailure { Runtime.toast(it.message ?: "couldn't share") }
                }
            }.padding(horizontal = 20.dp, vertical = 10.dp), style = MaterialTheme.typography.bodyMedium)
        }
    }
}
