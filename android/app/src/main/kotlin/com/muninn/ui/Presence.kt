package com.muninn.ui

import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.scale
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import com.muninn.PeerBook
import com.muninn.accents

/**
 * The single source of truth for what a peer's connectivity looks like.
 *
 * Shared by the top bar and every peer row. Drawing these independently let
 * the two disagree about the same peer, which reads as a bug rather than as
 * two views of one fact. Matches PresenceDot.qml on the desktop.
 */
@Composable
fun PresenceDot(
    state: PeerBook.State,
    unreachable: Boolean = false,
    size: androidx.compose.ui.unit.Dp = 9.dp,
    modifier: Modifier = Modifier,
) {
    val target = presenceColor(state, unreachable)
    val color by animateColorAsState(target, tween(220), label = "presence")

    Box(modifier = modifier.size(size * 2.4f), contentAlignment = Alignment.Center) {
        // A slow halo on a live link — the one state worth drawing the eye to.
        if (state == PeerBook.State.CONNECTED) {
            val pulse = rememberInfiniteTransition(label = "pulse")
            val scale by pulse.animateFloat(
                initialValue = 1f,
                targetValue = 2.2f,
                animationSpec = infiniteRepeatable(tween(1600), RepeatMode.Restart),
                label = "scale",
            )
            val fade by pulse.animateFloat(
                initialValue = 0.45f,
                targetValue = 0f,
                animationSpec = infiniteRepeatable(tween(1600), RepeatMode.Restart),
                label = "fade",
            )
            Box(
                Modifier
                    .size(size)
                    .scale(scale)
                    .clip(CircleShape)
                    .background(color.copy(alpha = fade)),
            )
        }
        Box(Modifier.size(size).clip(CircleShape).background(color))
    }
}

/**
 * A device that is right there and still will not connect is a different
 * problem from one out of range, so it gets its own colour rather than being
 * lumped in with "offline".
 */
@Composable
fun presenceColor(state: PeerBook.State, unreachable: Boolean = false): Color = when {
    state == PeerBook.State.CONNECTED -> MaterialTheme.accents.success
    unreachable -> MaterialTheme.colorScheme.error
    state == PeerBook.State.RELAY -> MaterialTheme.accents.warning
    state == PeerBook.State.NEARBY -> MaterialTheme.accents.warning.copy(alpha = 0.65f)
    else -> MaterialTheme.colorScheme.outline
}
