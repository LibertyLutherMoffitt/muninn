package com.muninn.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.defaultMinSize
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExtendedFloatingActionButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.muninn.ChatRepository
import com.muninn.PeerBook
import com.muninn.ScanPolicy
import com.muninn.formatWhen

/**
 * Home: one row per person Muninn knows and one per group, most recent first.
 *
 * Everyone you have ever exchanged keys with — directly or through someone
 * else — is listed, reachable or not, because you can write to all of them:
 * a message to someone out of range waits and goes out on its own.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ConversationListScreen(
    onOpen: (String) -> Unit,
    onNewGroup: () -> Unit,
    onShowPeers: () -> Unit,
    onPair: () -> Unit,
    onScanMode: () -> Unit,
    onYourName: () -> Unit,
    scanPolicy: ScanPolicy,
) {
    val conversations by ChatRepository.conversations.collectAsState()
    val presence by ChatRepository.presence.collectAsState()
    val statuses = presence.associateBy { it.wireId }
    var menu by remember { mutableStateOf(false) }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text("Muninn", fontWeight = FontWeight.SemiBold)
                        StatusLine(presence)
                    }
                },
                actions = {
                    TextButton(onClick = onShowPeers) { Text("Nearby") }
                    IconButton(onClick = { menu = true }) {
                        Icon(Icons.Filled.MoreVert, contentDescription = "More")
                    }
                    DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
                        DropdownMenuItem(
                            text = { Text("Your name") },
                            onClick = { menu = false; onYourName() },
                        )
                        DropdownMenuItem(
                            text = { Text("Scan mode: ${scanPolicy.label}") },
                            onClick = { menu = false; onScanMode() },
                        )
                        DropdownMenuItem(
                            text = { Text("Pair a device") },
                            onClick = { menu = false; onPair() },
                        )
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.surface,
                ),
            )
        },
        floatingActionButton = {
            if (conversations.any { !it.isGroup }) {
                ExtendedFloatingActionButton(
                    onClick = onNewGroup,
                    icon = { Icon(Icons.Filled.Add, contentDescription = null) },
                    text = { Text("New group") },
                )
            }
        },
        containerColor = MaterialTheme.colorScheme.background,
    ) { padding ->
        if (conversations.isEmpty()) {
            EmptyState(
                glyph = "◌",
                title = "Looking for people nearby",
                subtitle = "Muninn keeps scanning in the background. Anyone running it " +
                    "within Bluetooth range shows up here on their own — and so " +
                    "does anyone they can reach.",
                hint = "The first time, one of you may need \u22ee \u203a Pair a device.",
                modifier = Modifier.padding(padding),
            )
        } else {
            LazyColumn(
                modifier = Modifier.fillMaxSize().padding(padding),
                contentPadding = PaddingValues(bottom = 96.dp),
            ) {
                items(conversations, key = { it.id }) { conv ->
                    ConversationRow(conv, conv.peer?.let(statuses::get), onClick = { onOpen(conv.id) })
                }
            }
        }
    }
}

@Composable
private fun StatusLine(presence: List<PeerBook.PeerStatus>) {
    val connected = presence.count { it.state == PeerBook.State.CONNECTED }
    val relayed = presence.count { it.state == PeerBook.State.RELAY }
    val stuck = presence.count { it.unreachableNearby }
    val text = when {
        connected > 0 && relayed > 0 -> "$connected connected · $relayed more through them"
        connected == 1 -> "Connected to ${ChatRepository.displayName(presence.first { it.state == PeerBook.State.CONNECTED }.wireId)}"
        connected > 1 -> "$connected people connected"
        stuck > 0 -> "$stuck nearby, not connecting"
        else -> "Looking for people nearby"
    }
    Row(verticalAlignment = Alignment.CenterVertically) {
        PresenceDot(
            if (connected > 0) PeerBook.State.CONNECTED else PeerBook.State.OFFLINE,
            unreachable = connected == 0 && stuck > 0,
            size = 7.dp,
        )
        Text(
            text,
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )
    }
}

@Composable
private fun ConversationRow(
    conv: ChatRepository.Conversation,
    status: PeerBook.PeerStatus?,
    onClick: () -> Unit,
) {
    Row(
        verticalAlignment = Alignment.CenterVertically,
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
            .padding(horizontal = 16.dp, vertical = 10.dp),
    ) {
        Avatar(conv.title, isGroup = conv.isGroup, presence = status)
        Spacer(Modifier.width(14.dp))
        Column(Modifier.weight(1f)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    conv.title,
                    style = MaterialTheme.typography.bodyLarge,
                    fontWeight = if (conv.unread > 0) FontWeight.Bold else FontWeight.Medium,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f),
                )
                conv.last?.let {
                    Text(
                        formatWhen(it.timestamp),
                        style = MaterialTheme.typography.labelSmall,
                        color = if (conv.unread > 0) {
                            MaterialTheme.colorScheme.primary
                        } else {
                            MaterialTheme.colorScheme.outline
                        },
                    )
                }
            }
            Spacer(Modifier.height(2.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    subtitle(conv, status),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f),
                )
                if (conv.unread > 0) {
                    Spacer(Modifier.width(8.dp))
                    Box(
                        Modifier
                            .defaultMinSize(minWidth = 20.dp, minHeight = 20.dp)
                            .clip(CircleShape)
                            .background(MaterialTheme.colorScheme.primary)
                            .padding(horizontal = 6.dp),
                        contentAlignment = Alignment.Center,
                    ) {
                        Text(
                            if (conv.unread > 99) "99+" else conv.unread.toString(),
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onPrimary,
                            fontWeight = FontWeight.Bold,
                        )
                    }
                }
            }
        }
    }
}

/** The last message if there is one, else how reachable the person is. */
private fun subtitle(conv: ChatRepository.Conversation, status: PeerBook.PeerStatus?): String {
    val last = conv.last
    if (last != null) {
        val who = when {
            last.outgoing -> "You: "
            conv.isGroup -> "${ChatRepository.displayName(last.peer)}: "
            else -> ""
        }
        return who + last.text.replace('\n', ' ')
    }
    if (conv.isGroup) return "${conv.members.size} members"
    return status?.let { presenceText(it) } ?: "not seen yet"
}

/** One line on a peer's reachability, with names instead of addresses. */
fun presenceText(status: PeerBook.PeerStatus): String =
    if (status.state == PeerBook.State.RELAY) {
        status.relayText(ChatRepository::displayName)
    } else {
        status.describe()
    }
