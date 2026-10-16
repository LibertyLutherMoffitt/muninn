package com.muninn

import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.net.Uri
import android.provider.Settings
import android.util.Log
import com.muninn.core.Json
import com.muninn.core.list
import com.muninn.core.str
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.io.File
import java.net.HttpURLConnection
import java.net.URL

/**
 * Self-update from the project's GitHub releases (when there's internet).
 *
 * The APK is installed through a PackageInstaller session. Android always
 * verifies it's signed with the same key as the installed app, so a tampered
 * download can't replace Muninn. From Android 12 on, updates of an app that
 * installed itself last time can go through without a prompt; the very first
 * one (after an adb/manual install) asks once.
 */
object Updater {
    private const val TAG = "Updater"
    private const val ASSET = "muninn-android.apk"
    private const val CHECK_EVERY_MS = 12 * 3600 * 1000L

    sealed class State {
        data object Idle : State()
        data object Checking : State()
        data class UpToDate(val version: String) : State()
        data class Available(val version: String, val url: String, val notes: String, val sizeBytes: Long) : State()
        data class Downloading(val version: String, val progress: Float) : State()
        data class Installing(val version: String) : State()
        data class Failed(val message: String) : State()
    }

    private val _state = MutableStateFlow<State>(State.Idle)
    val state: StateFlow<State> = _state.asStateFlow()

    val current: String get() = BuildConfig.VERSION_NAME

    /** Compare dotted versions numerically ("0.10.0" > "0.9.3"). */
    fun newer(a: String, b: String): Boolean {
        fun parts(v: String) = v.trimStart('v').split('.', '-').map { p -> p.takeWhile(Char::isDigit).toIntOrNull() ?: 0 }
        val x = parts(a); val y = parts(b)
        for (i in 0 until maxOf(x.size, y.size)) {
            val d = x.getOrElse(i) { 0 } - y.getOrElse(i) { 0 }
            if (d != 0) return d > 0
        }
        return false
    }

    /** Background check at most every 12 h (call from IO). */
    fun maybeCheck(ctx: Context) {
        val p = Runtime.prefs(ctx)
        if (System.currentTimeMillis() - p.getLong("update_checked", 0) < CHECK_EVERY_MS) return
        check(ctx)
    }

    /** Ask GitHub for the newest release (call from IO). */
    fun check(ctx: Context) {
        _state.value = State.Checking
        try {
            val c = URL("https://api.github.com/repos/${BuildConfig.UPDATE_REPO}/releases/latest")
                .openConnection() as HttpURLConnection
            c.connectTimeout = 8000; c.readTimeout = 8000
            c.setRequestProperty("Accept", "application/vnd.github+json")
            if (c.responseCode == 404) { _state.value = State.UpToDate(current); return }
            val j = Json.obj(c.inputStream.bufferedReader().readText())
            Runtime.prefs(ctx).edit().putLong("update_checked", System.currentTimeMillis()).apply()
            val tag = j.str("tag_name").trimStart('v')
            @Suppress("UNCHECKED_CAST")
            val asset = j.list("assets").map { it as Map<String, Any?> }.firstOrNull { it.str("name") == ASSET }
            _state.value = if (asset != null && newer(tag, current))
                State.Available(tag, asset.str("browser_download_url"), j.str("body"), (asset["size"] as? Number)?.toLong() ?: 0)
            else State.UpToDate(current)
        } catch (e: Exception) {
            Log.w(TAG, "check failed", e)
            _state.value = State.Failed("No connection to GitHub — try again when you have internet")
        }
    }

    /** True if Android lets us install; otherwise opens the per-app setting. */
    fun ensurePermission(ctx: Context): Boolean {
        if (ctx.packageManager.canRequestPackageInstalls()) return true
        ctx.startActivity(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:${ctx.packageName}"))
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        Runtime.toast("Allow Muninn to install updates, then tap Install again")
        return false
    }

    /** Download + install the available release (call from IO). */
    fun install(ctx: Context) {
        val a = _state.value as? State.Available ?: return
        try {
            val dir = File(ctx.cacheDir, "updates").apply { mkdirs(); listFiles()?.forEach { it.delete() } }
            val apk = File(dir, "muninn-${a.version}.apk")
            var c = URL(a.url).openConnection() as HttpURLConnection
            c.instanceFollowRedirects = true
            c.connectTimeout = 10000; c.readTimeout = 20000
            // GitHub redirects to its CDN (another host): follow manually.
            var hops = 0
            while (c.responseCode in 301..308 && hops++ < 5) {
                c = URL(c.getHeaderField("Location")).openConnection() as HttpURLConnection
            }
            val total = c.contentLengthLong.takeIf { it > 0 } ?: a.sizeBytes
            c.inputStream.use { input ->
                apk.outputStream().use { out ->
                    val buf = ByteArray(64 * 1024); var n: Int; var got = 0L
                    while (input.read(buf).also { n = it } >= 0) {
                        out.write(buf, 0, n); got += n
                        if (total > 0) _state.value = State.Downloading(a.version, got.toFloat() / total)
                    }
                }
            }
            _state.value = State.Installing(a.version)
            val pi = ctx.packageManager.packageInstaller
            val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL).apply {
                setAppPackageName(ctx.packageName)
                setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED)
            }
            val id = pi.createSession(params)
            pi.openSession(id).use { s ->
                s.openWrite("muninn.apk", 0, apk.length()).use { out -> apk.inputStream().use { it.copyTo(out) }; s.fsync(out) }
                val status = PendingIntent.getBroadcast(ctx, id, Intent(ctx, InstallReceiver::class.java),
                    PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_MUTABLE)
                s.commit(status.intentSender)
            }
        } catch (e: Exception) {
            Log.w(TAG, "install failed", e)
            _state.value = State.Failed("Update failed: ${e.message}")
        }
    }

    internal fun failed(msg: String) { _state.value = State.Failed(msg) }
}

/** PackageInstaller results: confirm prompt, success (we get restarted), errors. */
class InstallReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        when (val st = intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE)) {
            PackageInstaller.STATUS_PENDING_USER_ACTION -> {
                @Suppress("DEPRECATION")
                val confirm = intent.getParcelableExtra<Intent>(Intent.EXTRA_INTENT) ?: return
                ctx.startActivity(confirm.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            }
            PackageInstaller.STATUS_SUCCESS -> Runtime.toast("Muninn updated")
            else -> {
                val msg = intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE) ?: "error $st"
                Updater.failed(
                    if (st == PackageInstaller.STATUS_FAILURE_CONFLICT || st == PackageInstaller.STATUS_FAILURE_INCOMPATIBLE)
                        "This update is signed differently from the installed app ($msg). Reinstall once from the release page."
                    else "Update didn't install: $msg"
                )
            }
        }
    }
}
