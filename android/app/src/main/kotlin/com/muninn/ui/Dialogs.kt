package com.muninn.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Checkbox
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.muninn.ChatRepository

/** Ask for a name: your own, or a local nickname for someone else. */
@Composable
fun NameDialog(
    title: String,
    explanation: String,
    initial: String,
    placeholder: String,
    onDismiss: () -> Unit,
    onSave: (String) -> Unit,
) {
    var value by remember { mutableStateOf(initial) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(title) },
        text = {
            Column {
                Text(explanation, style = MaterialTheme.typography.bodySmall)
                Spacer(Modifier.padding(4.dp))
                OutlinedTextField(
                    value = value,
                    onValueChange = { value = it.take(64) },
                    singleLine = true,
                    placeholder = { Text(placeholder) },
                    modifier = Modifier.fillMaxWidth(),
                )
            }
        },
        confirmButton = { TextButton(onClick = { onSave(value.trim()) }) { Text("Save") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}

/**
 * Name a group and pick who is in it. Anyone Muninn knows can be added,
 * reachable or not: the group reaches them when they are next in range, or
 * through whoever is.
 */
@Composable
fun NewGroupDialog(onDismiss: () -> Unit, onCreate: (String, List<String>) -> Unit) {
    val conversations by ChatRepository.conversations.collectAsState()
    val presence by ChatRepository.presence.collectAsState()
    val statuses = presence.associateBy { it.wireId }
    val people = conversations.filter { !it.isGroup && it.peer != null && ChatRepository.book.pubkey(it.peer) != null }
    var name by remember { mutableStateOf("") }
    val chosen = remember { mutableStateListOf<String>() }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("New group") },
        text = {
            Column {
                OutlinedTextField(
                    value = name,
                    onValueChange = { name = it.take(64) },
                    singleLine = true,
                    label = { Text("Group name") },
                    modifier = Modifier.fillMaxWidth(),
                )
                Spacer(Modifier.padding(6.dp))
                Text("Who's in it", style = MaterialTheme.typography.labelMedium)
                LazyColumn(Modifier.heightIn(max = 320.dp)) {
                    items(people, key = { it.id }) { person ->
                        val peer = person.peer!!
                        val picked = peer in chosen
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            modifier = Modifier
                                .fillMaxWidth()
                                .clickable { if (picked) chosen.remove(peer) else chosen.add(peer) }
                                .padding(vertical = 4.dp),
                        ) {
                            Checkbox(checked = picked, onCheckedChange = null)
                            Spacer(Modifier.width(8.dp))
                            Avatar(person.title, presence = statuses[peer], size = 30.dp)
                            Spacer(Modifier.width(10.dp))
                            Column {
                                Text(
                                    person.title,
                                    fontWeight = FontWeight.Medium,
                                    maxLines = 1,
                                    overflow = TextOverflow.Ellipsis,
                                )
                                Text(
                                    statuses[peer]?.let(::presenceText) ?: "not seen yet",
                                    style = MaterialTheme.typography.labelSmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                        }
                    }
                }
            }
        },
        confirmButton = {
            TextButton(
                enabled = name.isNotBlank() && chosen.isNotEmpty(),
                onClick = { onCreate(name.trim(), chosen.toList()) },
            ) { Text("Create") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}
