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
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.muninn.ScanPolicy

@Composable
fun ScanModeDialog(
    current: ScanPolicy,
    onDismiss: () -> Unit,
    onPick: (ScanPolicy) -> Unit,
    onMakeDiscoverable: () -> Unit,
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("How hard to look for peers") },
        text = {
            Column {
                Text(
                    "Scanning finds people sooner but costs battery.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Spacer(Modifier.height(12.dp))
                ScanPolicy.entries.forEach { policy ->
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        modifier = Modifier
                            .fillMaxWidth()
                            .clickable { onPick(policy) }
                            .padding(vertical = 8.dp),
                    ) {
                        RadioButton(
                            selected = policy == current,
                            onClick = { onPick(policy) },
                        )
                        Spacer(Modifier.width(4.dp))
                        Column {
                            Text(policy.label, style = MaterialTheme.typography.bodyMedium)
                            Text(
                                policy.description,
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
                }
                Spacer(Modifier.height(8.dp))
                Text(
                    "Other devices can only find this phone while it is " +
                        "discoverable. Android limits that to a few minutes at a time.",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        },
        confirmButton = {
            TextButton(onClick = onMakeDiscoverable) { Text("Make discoverable") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Close") } },
    )
}
