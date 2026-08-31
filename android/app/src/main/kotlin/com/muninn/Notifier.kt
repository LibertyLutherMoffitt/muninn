package com.muninn

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import java.util.concurrent.ConcurrentHashMap

/**
 * System notifications for incoming messages.
 *
 * Muninn is for the times you are not looking at your phone — a flight, a
 * queue, a car. Without this the app can only be read by opening it, which
 * defeats the point: a message would land silently and the radio work behind
 * it would go unnoticed.
 *
 * One notification per peer, using [Notification.MessagingStyle] so the system
 * renders it as a conversation (and so Android can surface it in the
 * conversation shade on versions that have one). Content is marked private, so
 * a locked screen hides the text unless the user has chosen otherwise —
 * sensible for a messenger whose whole premise is not trusting the network.
 */
class Notifier(private val ctx: Context) {

    private val manager = ctx.getSystemService(NotificationManager::class.java)

    // Message history per peer, so a second message from someone extends their
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

    /** Post (or extend) the notification for `message`'s sender. */
    fun notifyMessage(message: ChatRepository.Message) {
        if (message.outgoing) return
        val peer = message.peer
        val history = threads.getOrPut(peer) { mutableListOf() }
        synchronized(history) {
            history.add(message)
            // Android renders a handful of lines at most; keeping the whole
            // thread here would grow without bound for no visible gain.
            while (history.size > MAX_LINES) history.removeAt(0)
        }

        val name = ChatRepository.displayName(peer)
        val them = android.app.Person.Builder().setName(name).setKey(peer).build()
        val style = Notification.MessagingStyle(
            android.app.Person.Builder().setName("You").setKey("self").build(),
        )
        synchronized(history) {
            for (m in history) {
                style.addMessage(m.text, m.timestamp, if (m.outgoing) null else them)
            }
        }

        val notification = Notification.Builder(ctx, MESSAGE_CHANNEL)
            .setSmallIcon(android.R.drawable.stat_notify_chat)
            .setStyle(style)
            .setContentIntent(openApp())
            .setAutoCancel(true)
            .setCategory(Notification.CATEGORY_MESSAGE)
            // Hide the text on a locked screen unless the user opts in.
            .setVisibility(Notification.VISIBILITY_PRIVATE)
            .setWhen(message.timestamp)
            .setShowWhen(true)
            .build()

        runCatching { manager.notify(peer, MESSAGE_ID, notification) }
    }

    /** Clear a peer's notification — they have been read. */
    fun clear(peer: String) {
        threads.remove(peer)
        runCatching { manager.cancel(peer, MESSAGE_ID) }
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

    private fun openApp(): PendingIntent {
        val intent = Intent(ctx, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
        }
        var flags = PendingIntent.FLAG_UPDATE_CURRENT
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            flags = flags or PendingIntent.FLAG_IMMUTABLE
        }
        return PendingIntent.getActivity(ctx, 0, intent, flags)
    }

    companion object {
        const val RADIO_CHANNEL = "muninn.radio"
        const val MESSAGE_CHANNEL = "muninn.messages"
        const val RADIO_ID = 1
        /** Shared id; the per-peer tag is what separates the notifications. */
        const val MESSAGE_ID = 2
        private const val MAX_LINES = 8
    }
}
