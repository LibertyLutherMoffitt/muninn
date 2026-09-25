package com.muninn

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import java.util.concurrent.ConcurrentHashMap

/**
 * System notifications for incoming messages.
 *
 * Muninn is for the times you are not looking at your phone — a flight, a
 * queue, a car. Without this the app can only be read by opening it, which
 * defeats the point: a message would land silently and the radio work behind
 * it would go unnoticed.
 *
 * One notification per conversation, using [Notification.MessagingStyle] so the system
 * renders it as a conversation (and so Android can surface it in the
 * conversation shade on versions that have one). Content is marked private, so
 * a locked screen hides the text unless the user has chosen otherwise —
 * sensible for a messenger whose whole premise is not trusting the network.
 */
class Notifier(private val ctx: Context) {

    private val manager = ctx.getSystemService(NotificationManager::class.java)

    // Message history per conversation, so a second message extends the
    // existing notification rather than replacing it.
    private val threads = ConcurrentHashMap<String, MutableList<ChatRepository.Message>>()

    fun ensureChannels() {
        manager.createNotificationChannel(
            NotificationChannel(
                RADIO_CHANNEL,
                "Muninn radio",
                NotificationManager.IMPORTANCE_LOW,
            ).apply {
                description = "Keeps the Bluetooth socket alive in the background."
                setShowBadge(false)
            },
        )
        manager.createNotificationChannel(
            NotificationChannel(
                MESSAGE_CHANNEL,
                "Messages",
                // HIGH so it makes a sound and shows a heads-up: the whole
                // point is to reach someone who is not looking at the app.
                NotificationManager.IMPORTANCE_HIGH,
            ).apply {
                description = "Someone nearby sent you a message."
                enableVibration(true)
            },
        )
    }

    /** Post (or extend) the notification for `message`'s conversation. */
    fun notifyMessage(message: ChatRepository.Message) {
        if (message.outgoing) return
        val conv = message.conv.ifEmpty { ChatRepository.dmId(message.peer) }
        val history = threads.getOrPut(conv) { mutableListOf() }
        synchronized(history) {
            history.add(message)
            // Android renders a handful of lines at most; keeping the whole
            // thread here would grow without bound for no visible gain.
            while (history.size > MAX_LINES) history.removeAt(0)
        }

        val isGroup = conv.startsWith("group:")
        val style = Notification.MessagingStyle(
            android.app.Person.Builder().setName("You").setKey("self").build(),
        )
        if (isGroup) {
            style.setConversationTitle(ChatRepository.title(conv))
            style.setGroupConversation(true)
        }
        synchronized(history) {
            for (m in history) {
                val sender = android.app.Person.Builder()
                    .setName(ChatRepository.displayName(m.peer))
                    .setKey(m.peer)
                    .build()
                style.addMessage(m.text, m.timestamp, sender)
            }
        }

        val notification = Notification.Builder(ctx, MESSAGE_CHANNEL)
            .setSmallIcon(android.R.drawable.stat_notify_chat)
            .setStyle(style)
            .setContentIntent(openApp(conv))
            .setAutoCancel(true)
            .setCategory(Notification.CATEGORY_MESSAGE)
            // Hide the text on a locked screen unless the user opts in.
            .setVisibility(Notification.VISIBILITY_PRIVATE)
            .setWhen(message.timestamp)
            .setShowWhen(true)
            .build()

        runCatching { manager.notify(conv, MESSAGE_ID, notification) }
    }

    /** Clear a conversation's notification — it has been read. */
    fun clear(conv: String) {
        threads.remove(conv)
        runCatching { manager.cancel(conv, MESSAGE_ID) }
    }

    fun clearAll() {
        threads.keys.toList().forEach(::clear)
    }

    /**
     * The ongoing foreground notification. Says what the radio is actually
     * doing, because a permanent icon that only ever reads "running" is noise
     * the user learns to ignore.
     */
    fun radioNotification(text: String): Notification =
        Notification.Builder(ctx, RADIO_CHANNEL)
            .setContentTitle("Muninn")
            .setContentText(text)
            .setSmallIcon(android.R.drawable.stat_sys_data_bluetooth)
            .setContentIntent(openApp())
            .setOngoing(true)
            .setShowWhen(false)
            .build()

    fun updateRadio(text: String) {
        runCatching { manager.notify(RADIO_ID, radioNotification(text)) }
    }

    /** Open the app — straight into `conv` when there is one. */
    private fun openApp(conv: String? = null): PendingIntent {
        val intent = Intent(ctx, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
            if (conv != null) putExtra(EXTRA_CONVERSATION, conv)
        }
        val flags = PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        // A distinct request code per conversation, or every notification
        // would share one PendingIntent and open whichever was posted last.
        return PendingIntent.getActivity(ctx, conv?.hashCode() ?: 0, intent, flags)
    }

    companion object {
        const val EXTRA_CONVERSATION = "com.muninn.conversation"
        const val RADIO_CHANNEL = "muninn.radio"
        const val MESSAGE_CHANNEL = "muninn.messages"
        const val RADIO_ID = 1
        /** Shared id; the per-conversation tag is what separates the notifications. */
        const val MESSAGE_ID = 2
        private const val MAX_LINES = 8
    }
}
