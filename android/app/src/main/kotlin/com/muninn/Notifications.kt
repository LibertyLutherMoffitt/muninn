package com.muninn

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.RemoteInput
import android.app.Person
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import com.muninn.core.AppEvent
import com.muninn.core.GameHost
import com.muninn.core.StoredMessage

object Notifications {
    const val CH_RADIO = "muninn.radio"
    const val CH_MSG = "muninn.messages"
    const val CH_GAME = "muninn.games"
    const val RADIO_ID = 1
    const val KEY_REPLY = "reply"

    fun ensureChannels(ctx: Context) {
        val m = ctx.getSystemService(NotificationManager::class.java)
        m.createNotificationChannel(NotificationChannel(CH_RADIO, "Bluetooth link", NotificationManager.IMPORTANCE_MIN).apply {
            description = "Keeps Muninn connected in the background."
        })
        m.createNotificationChannel(NotificationChannel(CH_MSG, "Messages", NotificationManager.IMPORTANCE_HIGH))
        m.createNotificationChannel(NotificationChannel(CH_GAME, "Game invites", NotificationManager.IMPORTANCE_DEFAULT))
    }

    private fun openIntent(ctx: Context, conv: String?, session: String? = null): PendingIntent {
        val i = Intent(ctx, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP
            conv?.let { putExtra("conv", it) }
            session?.let { putExtra("game", it) }
        }
        return PendingIntent.getActivity(ctx, (conv + session).hashCode(), i, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
    }

    fun radio(ctx: Context, text: String): Notification =
        Notification.Builder(ctx, CH_RADIO)
            .setContentTitle("Muninn")
            .setContentText(text)
            .setSmallIcon(android.R.drawable.stat_sys_data_bluetooth)
            .setOngoing(true)
            .setContentIntent(openIntent(ctx, null))
            .build()

    fun message(ctx: Context, m: StoredMessage) {
        val node = Runtime.node ?: return
        val mesh = node.mesh
        val conv = m.conv
        val unread = mesh.storage.loadHistory(conv, 50).filter { !it.readLocal && it.sender != node.nodeId && (it.kind == "text" || it.kind == "file") }
            .takeLast(8)
        if (unread.isEmpty()) return
        val me = Person.Builder().setName("You").setKey(node.nodeId).build()
        val style = Notification.MessagingStyle(me)
        if (conv.startsWith("group:")) { style.conversationTitle = Runtime.convTitle(conv); style.isGroupConversation = true }
        for (u in unread) {
            val p = Person.Builder().setName(mesh.displayName(u.sender)).setKey(u.sender).build()
            val text = if (u.kind == "file") "📎 " + (u.text.ifEmpty { u.meta["n"] as? String ?: "file" }) else u.text
            style.addMessage(Notification.MessagingStyle.Message(text, u.ts * 1000, p))
        }
        val replyIntent = PendingIntent.getBroadcast(ctx, conv.hashCode(),
            Intent(ctx, ReplyReceiver::class.java).putExtra("conv", conv),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_MUTABLE)
        val reply = Notification.Action.Builder(null, "Reply", replyIntent)
            .addRemoteInput(RemoteInput.Builder(KEY_REPLY).setLabel("Message").build()).build()
        val readIntent = PendingIntent.getBroadcast(ctx, conv.hashCode() + 1,
            Intent(ctx, ReplyReceiver::class.java).putExtra("conv", conv).putExtra("read", true),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        // Lock screen shows only "New message" unless the user opts in.
        val public = Notification.Builder(ctx, CH_MSG)
            .setSmallIcon(R.drawable.ic_launcher_monochrome)
            .setContentTitle("Muninn")
            .setContentText(if (unread.size == 1) "New message" else "${unread.size} new messages")
            .build()
        val n = Notification.Builder(ctx, CH_MSG)
            .setSmallIcon(R.drawable.ic_launcher_monochrome)
            .setVisibility(Notification.VISIBILITY_PRIVATE)
            .setPublicVersion(public)
            .setStyle(style)
            .setContentIntent(openIntent(ctx, conv))
            .setAutoCancel(true)
            .setCategory(Notification.CATEGORY_MESSAGE)
            .addAction(reply)
            .addAction(Notification.Action.Builder(null, "Mark read", readIntent).build())
            .build()
        ctx.getSystemService(NotificationManager::class.java).notify(conv.hashCode(), n)
    }

    fun cancel(ctx: Context, conv: String) = ctx.getSystemService(NotificationManager::class.java).cancel(conv.hashCode())

    fun gameInvite(ctx: Context, ev: AppEvent, host: GameHost) {
        val info = host.sessionInfo(ev.session)
        val who = Runtime.node?.mesh?.displayName(ev.sender) ?: "Someone"
        val n = Notification.Builder(ctx, CH_GAME)
            .setSmallIcon(android.R.drawable.ic_media_play)
            .setContentTitle("$who wants to play")
            .setContentText("${info["icon"] ?: "🎮"} ${info["name"] ?: ev.app}")
            .setContentIntent(openIntent(ctx, ev.conv, ev.session))
            .setAutoCancel(true)
            .build()
        ctx.getSystemService(NotificationManager::class.java).notify(ev.session.hashCode(), n)
    }
}

/** Inline reply / mark-read from a message notification. */
class ReplyReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        val conv = intent.getStringExtra("conv") ?: return
        val node = Runtime.node ?: return
        if (!intent.getBooleanExtra("read", false)) {
            val text = RemoteInput.getResultsFromIntent(intent)?.getCharSequence(Notifications.KEY_REPLY)?.toString()
            if (!text.isNullOrBlank()) runCatching { node.mesh.sendText(conv, Emoji.expand(text.trim())) }
        }
        node.mesh.markRead(conv)
        Notifications.cancel(ctx, conv)
        Runtime.bump()
    }
}

/** Restart the radio after reboot (if the user hasn't turned that off). */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        val updated = intent.action == Intent.ACTION_MY_PACKAGE_REPLACED
        if (intent.action != Intent.ACTION_BOOT_COMPLETED && !updated) return
        // after a self-update the radio was running a moment ago: always resume
        if (!updated && !Runtime.prefs(ctx).getBoolean("start_on_boot", true)) return
        runCatching { ctx.startForegroundService(Intent(ctx, MuninnService::class.java)) }
    }
}
