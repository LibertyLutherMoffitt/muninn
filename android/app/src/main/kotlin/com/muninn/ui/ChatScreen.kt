package com.muninn.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.ime
import androidx.compose.foundation.layout.navigationBars
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.union
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.Info
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.muninn.ChatRepository
import com.muninn.MessageGrouping
import com.muninn.PeerBook
import com.muninn.toHex

/**
 * One conversation — a person or a group.
 *
 * The header says plainly whether a message sent now goes straight out,
 * travels through someone else, or has to wait; the composer never locks,
 * because waiting is handled for you.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ChatScreen(conv: String, onBack: () -> Unit) {
    val all by ChatRepository.messages.collectAsState()
    val presence by ChatRepository.presence.collectAsState()
    // Re-read on every conversation-list change so a group created or renamed
    // while open is reflected.
    val conversations by ChatRepository.conversations.collectAsState()
    val messages = remember(all, conv) { all.filter { it.conv == conv } }
    val statuses = presence.associateBy { it.wireId }
    val isGroup = conv.startsWith("group:")
    val title = conversations.firstOrNull { it.id == conv }?.title ?: ChatRepository.title(conv)
    val recipients = remember(conversations, conv) { ChatRepository.recipients(conv) }
    val unreachable = recipients.filter { statuses[it]?.isReachable != true }

    var draft by rememberSaveable(conv) { mutableStateOf("") }
    var showRename by remember { mutableStateOf(false) }
    var showMembers by remember { mutableStateOf(false) }
    val listState = rememberLazyListState()

    LaunchedEffect(messages.size) {
        if (messages.isNotEmpty()) listState.animateScrollToItem(messages.size - 1)
    }

    Scaffold(
        topBar = {
            TopAppBar(
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
                    }
                },
                title = {
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        modifier = Modifier.clickable(enabled = isGroup) { showMembers = true },
                    ) {
                        val status = if (isGroup) null else statuses[recipients.firstOrNull()]
                        Avatar(title, isGroup = isGroup, presence = status, size = 36.dp)
                        Spacer(Modifier.width(10.dp))
                        Column {
                            Text(
                                title,
                                fontWeight = FontWeight.SemiBold,
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis,
                                style = MaterialTheme.typography.titleMedium,
                            )
                            Text(
                                headerLine(isGroup, recipients, statuses),
                                style = MaterialTheme.typography.labelSmall,
                                color = headerColor(isGroup, recipients, statuses),
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis,
                            )
                        }
                    }
                },
                actions = {
                    if (isGroup) {
                        IconButton(onClick = { showMembers = true }) {
                            Icon(Icons.Filled.Info, contentDescription = "Members")
                        }
                    } else {
                        IconButton(onClick = { showRename = true }) {
                            Icon(Icons.Filled.Edit, contentDescription = "Rename")
                        }
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.surface,
                ),
            )
        },
        bottomBar = {
            Column(
                // union (per-side max), not a chain (sum): with the keyboard
                // up, ime already covers the nav-bar region.
                Modifier.windowInsetsPadding(WindowInsets.navigationBars.union(WindowInsets.ime)),
            ) {
                if (unreachable.isNotEmpty() && recipients.isNotEmpty()) {
                    WaitingBanner(isGroup, recipients, unreachable)
                }
                Composer(
                    draft = draft,
                    onDraftChange = { draft = it },
                    enabled = recipients.isNotEmpty(),
                    // The banner above explains the wait; the hint stays short
                    // enough to fit a phone.
                    placeholder = if (unreachable.size == recipients.size && recipients.isNotEmpty()) {
                        "Message (will wait)"
                    } else {
                        "Message"
                    },
                    onSend = { if (ChatRepository.send(conv, draft)) draft = "" },
                )
            }
        },
        containerColor = MaterialTheme.colorScheme.background,
    ) { padding ->
        if (messages.isEmpty()) {
            EmptyState(
                glyph = if (isGroup) "#" else "✉",
                title = if (isGroup) "Say hello to the group" else "Say hello",
                subtitle = "Messages are encrypted end to end and travel over Bluetooth " +
                    "— straight to them, or through anyone nearby running Muninn. " +
                    "No network, no account.",
                modifier = Modifier.padding(padding),
            )
        } else {
            LazyColumn(
                state = listState,
                modifier = Modifier.fillMaxSize().padding(padding),
                contentPadding = PaddingValues(horizontal = 12.dp, vertical = 8.dp),
            ) {
                itemsIndexed(messages, key = { i, m -> m.msgId?.toHex() ?: "i$i" }) { index, msg ->
                    val previous = messages.getOrNull(index - 1)
                    val day = MessageGrouping.daySection(msg.timestamp)
                    if (previous == null || MessageGrouping.daySection(previous.timestamp) != day) {
                        DayDivider(day)
                    }
                    MessageBubble(
                        msg,
                        startsRun = MessageGrouping.startsRun(msg, previous),
                        // A DM needs no name on every run; a group does.
                        senderName = if (isGroup) ChatRepository.displayName(msg.peer) else "",
                        waiting = msg.outgoing &&
                            msg.ack == ChatRepository.Ack.SENT &&
                            msg.recipients.none { statuses[it]?.isReachable == true },
                    )
                }
            }
        }
    }

    if (showRename && !isGroup) {
        val peer = recipients.first()
        NameDialog(
            title = "Rename",
            explanation = "Only on this phone \u2014 they won't see it. Leave it empty " +
                "to go back to ${ChatRepository.book.selfChosenName(peer) ?: "their own name"}.",
            initial = "",
            placeholder = title,
            onDismiss = { showRename = false },
            onSave = {
                ChatRepository.rename(peer, it)
                showRename = false
            },
        )
    }

    if (showMembers && isGroup) {
        AlertDialog(
            onDismissRequest = { showMembers = false },
            confirmButton = { TextButton(onClick = { showMembers = false }) { Text("Done") } },
            title = { Text(title) },
            text = {
                Column {
                    Text("You", fontWeight = FontWeight.Medium, modifier = Modifier.padding(vertical = 6.dp))
                    for (m in recipients) {
                        val s = statuses[m]
                        Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(vertical = 6.dp)) {
                            Avatar(ChatRepository.displayName(m), presence = s, size = 30.dp)
                            Spacer(Modifier.width(10.dp))
                            Column {
                                Text(ChatRepository.displayName(m), fontWeight = FontWeight.Medium)
                                Text(
                                    s?.let(::presenceText) ?: "not seen yet",
                                    style = MaterialTheme.typography.labelSmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                        }
                    }
                }
            },
        )
    }
}

/** A plain-words note under the messages when some recipients can't be reached. */
@Composable
private fun WaitingBanner(isGroup: Boolean, recipients: List<String>, unreachable: List<String>) {
    val names = unreachable.map(ChatRepository::displayName)
    val text = when {
        !isGroup ->
            "${names.first()} is out of reach right now. What you send waits here and goes out " +
                "on its own — directly, or through anyone nearby — once there's a path."
        unreachable.size == recipients.size ->
            "Nobody in this group is in reach. Messages wait and go out as each person reappears."
        else -> {
            val shown = names.take(3).joinToString(", ") + if (names.size > 3) " +${names.size - 3}" else ""
            "Waiting on $shown. Everyone else gets it now; they'll get it when they're back in reach."
        }
    }
    Surface(
        color = MaterialTheme.colorScheme.surfaceVariant,
        shape = RoundedCornerShape(12.dp),
        modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 4.dp),
    ) {
        Text(
            text,
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(horizontal = 14.dp, vertical = 10.dp),
        )
    }
}

private fun headerLine(
    isGroup: Boolean,
    recipients: List<String>,
    statuses: Map<String, PeerBook.PeerStatus>,
): String {
    if (!isGroup) {
        val s = recipients.firstOrNull()?.let { statuses[it] } ?: return "not seen yet"
        return presenceText(s)
    }
    val reachable = recipients.count { statuses[it]?.isReachable == true }
    return when {
        recipients.isEmpty() -> "just you"
        reachable == recipients.size -> if (recipients.size == 1) "in reach" else "everyone in reach"
        reachable == 0 -> "nobody in reach · messages wait"
        else -> "$reachable of ${recipients.size} in reach"
    }
}

/** The header line takes the colour of the dot beside it, so the two agree. */
@Composable
private fun headerColor(
    isGroup: Boolean,
    recipients: List<String>,
    statuses: Map<String, PeerBook.PeerStatus>,
): androidx.compose.ui.graphics.Color {
    if (isGroup) {
        return if (recipients.any { statuses[it]?.isReachable == true }) {
            presenceColor(PeerBook.State.CONNECTED)
        } else {
            MaterialTheme.colorScheme.onSurfaceVariant
        }
    }
    val s = statuses[recipients.firstOrNull()] ?: return MaterialTheme.colorScheme.onSurfaceVariant
    return if (s.state == PeerBook.State.OFFLINE) {
        MaterialTheme.colorScheme.onSurfaceVariant
    } else {
        presenceColor(s.state, s.unreachableNearby)
    }
}
