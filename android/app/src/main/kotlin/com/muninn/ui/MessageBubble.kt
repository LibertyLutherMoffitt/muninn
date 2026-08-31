package com.muninn.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.muninn.ChatRepository
import com.muninn.formatTime
import com.muninn.tick

/**
 * One message. Draws its own delivery state and, at the head of a run, who
 * sent it.
 */
@Composable
fun MessageBubble(
    msg: ChatRepository.Message,
    startsRun: Boolean = true,
    senderName: String = "",
) {
    val outgoing = msg.outgoing
    val bubbleColor =
        if (outgoing) MaterialTheme.colorScheme.primaryContainer
        else MaterialTheme.colorScheme.surfaceVariant
    val textColor =
        if (outgoing) MaterialTheme.colorScheme.onPrimaryContainer
        else MaterialTheme.colorScheme.onSurfaceVariant
    // Flatten the corner facing the message above, so a burst from one
    // sender reads as a block rather than as loose pills.
    val shape = RoundedCornerShape(
        topStart = if (!outgoing && !startsRun) 6.dp else 18.dp,
        topEnd = if (outgoing && !startsRun) 6.dp else 18.dp,
        bottomStart = if (outgoing) 18.dp else 4.dp,
        bottomEnd = if (outgoing) 4.dp else 18.dp,
    )
    Column(
        modifier = Modifier.fillMaxWidth().padding(top = if (startsRun) 6.dp else 1.dp),
        horizontalAlignment = if (outgoing) Alignment.End else Alignment.Start,
    ) {
        // Only the first message of a run carries the name; repeating it on
        // every line of a burst is noise.
        if (!outgoing && startsRun && senderName.isNotEmpty()) {
            Text(
                senderName,
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.primary,
                fontWeight = FontWeight.SemiBold,
                modifier = Modifier.padding(start = 8.dp, bottom = 2.dp),
            )
        }
        Box(
            modifier = Modifier
                .widthIn(max = 300.dp)
                .clip(shape)
                .background(bubbleColor)
                .padding(horizontal = 14.dp, vertical = 9.dp),
        ) {
            Text(msg.text, color = textColor, style = MaterialTheme.typography.bodyMedium)
        }
        Text(
            if (outgoing) "${formatTime(msg.timestamp)}  ${msg.ack.tick()}"
            else formatTime(msg.timestamp),
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.outline,
            modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp),
        )
    }
}
