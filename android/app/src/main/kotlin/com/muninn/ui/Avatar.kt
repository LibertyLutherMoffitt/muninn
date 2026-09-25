package com.muninn.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import com.muninn.PeerBook

/**
 * A round initial for a person or group, with the person's presence dot on
 * its edge. The colour is derived from the name so someone keeps the same
 * colour everywhere without anyone choosing it.
 */
@Composable
fun Avatar(
    name: String,
    isGroup: Boolean = false,
    presence: PeerBook.PeerStatus? = null,
    size: Dp = 44.dp,
    modifier: Modifier = Modifier,
) {
    val palette = listOf(
        MaterialTheme.colorScheme.primaryContainer to MaterialTheme.colorScheme.onPrimaryContainer,
        MaterialTheme.colorScheme.secondaryContainer to MaterialTheme.colorScheme.onSecondaryContainer,
        MaterialTheme.colorScheme.tertiaryContainer to MaterialTheme.colorScheme.onTertiaryContainer,
    )
    val (bg, fg) = palette[Math.floorMod(name.hashCode(), palette.size)]
    val initial = name.trim().firstOrNull { it.isLetterOrDigit() }?.uppercase() ?: "?"
    Box(modifier.size(size)) {
        Box(
            Modifier.size(size).clip(CircleShape).background(bg),
            contentAlignment = Alignment.Center,
        ) {
            Text(
                if (isGroup) "#" else initial,
                color = fg,
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.SemiBold,
            )
        }
        if (presence != null) {
            Box(
                Modifier
                    .align(Alignment.BottomEnd)
                    .size(size * 0.34f)
                    .clip(CircleShape)
                    .background(MaterialTheme.colorScheme.surface)
                    .border(2.dp, MaterialTheme.colorScheme.surface, CircleShape),
                contentAlignment = Alignment.Center,
            ) {
                Box(
                    Modifier
                        .size(size * 0.22f)
                        .clip(CircleShape)
                        .background(presenceColor(presence.state, presence.unreachableNearby)),
                )
            }
        }
    }
}
