package com.muninn.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.muninn.ChatRepository
import com.muninn.PeerBook

/**
 * Who is around, and how reachable each of them is.
 *
 * The phone had no equivalent of the desktop's peer list — the only signal was
 * a single line in the top bar, so "connected" and "three people nearby, none
 * connecting" looked much the same. Wording matches the desktop's `:peers`
 * deliberately; two clients describing the same peer differently is confusing
 * in exactly the situation where you are checking whether the app works.
 */
@Composable
fun PeersSheet(statuses: List<PeerBook.PeerStatus>, modifier: Modifier = Modifier) {
    val ordered = statuses.sortedWith(
        compareBy({ it.state.ordinal }, { ChatRepository.displayName(it.wireId) }),
    )
    Column(modifier.navigationBarsPadding().padding(bottom = 12.dp)) {
        Text(
            "Peers",
            style = MaterialTheme.typography.titleMedium,
            modifier = Modifier.padding(start = 20.dp, end = 20.dp, bottom = 4.dp),
        )
        Text(
            summarise(ordered),
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(start = 20.dp, end = 20.dp, bottom = 12.dp),
        )
        HorizontalDivider()
        if (ordered.isEmpty()) {
            Column(
                Modifier.fillMaxWidth().padding(32.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                Text("Nobody yet", style = MaterialTheme.typography.titleMedium)
                Text(
                    "Muninn keeps looking in the background. Anyone running it " +
                        "nearby will appear here on their own.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        } else {
            LazyColumn {
                items(ordered, key = { it.wireId }) { status -> PeerRow(status) }
            }
        }
    }
}

private fun summarise(statuses: List<PeerBook.PeerStatus>): String {
    val connected = statuses.count { it.state == PeerBook.State.CONNECTED }
    val stuck = statuses.count { it.unreachableNearby }
    return when {
        connected > 0 && stuck > 0 -> "$connected connected · $stuck nearby but unreachable"
        connected > 0 -> "$connected connected"
        stuck > 0 -> "$stuck nearby, none connecting"
        statuses.isEmpty() -> "nothing found yet"
        else -> "${statuses.size} seen recently"
    }
}

@Composable
private fun PeerRow(status: PeerBook.PeerStatus) {
    Row(
        verticalAlignment = Alignment.CenterVertically,
        modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 10.dp),
    ) {
        PresenceDot(status.state, status.unreachableNearby)
        Spacer(Modifier.width(4.dp))
        Column(Modifier.weight(1f)) {
            Text(
                ChatRepository.displayName(status.wireId),
                style = MaterialTheme.typography.bodyMedium,
                fontWeight = FontWeight.Medium,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Spacer(Modifier.height(1.dp))
            Text(
                status.describe(),
                style = MaterialTheme.typography.labelSmall,
                color = if (status.unreachableNearby) {
                    MaterialTheme.colorScheme.error
                } else {
                    MaterialTheme.colorScheme.onSurfaceVariant
                },
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        }
    }
}
