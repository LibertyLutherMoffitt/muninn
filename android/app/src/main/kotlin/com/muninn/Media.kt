package com.muninn

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.ImageDecoder
import android.graphics.drawable.AnimatedImageDrawable
import android.graphics.drawable.Drawable
import android.net.Uri
import android.provider.OpenableColumns
import android.widget.ImageView
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.viewinterop.AndroidView
import com.muninn.core.Json
import com.muninn.core.list
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.ByteArrayOutputStream
import java.io.File

/** Shortcodes + categories from the shared emoji.json (python/src/muninn/assets). */
object Emoji {
    @Volatile private var data: Map<String, Any?>? = null
    private val codeRe = Regex(":([a-z0-9_+\\-]+):")

    fun init(ctx: Context) {
        if (data == null) data = Json.obj(ctx.assets.open("emoji.json").bufferedReader().readText())
    }

    @Suppress("UNCHECKED_CAST")
    val categories: List<Pair<String, List<Pair<String, String>>>>
        get() = ((data?.get("categories") as? Map<String, Any?>) ?: emptyMap()).map { (k, v) ->
            k to (v as List<List<String>>).map { it[0] to it[1] }
        }

    private val codes: Map<String, String> by lazy {
        val m = LinkedHashMap<String, String>()
        categories.forEach { (_, l) -> l.forEach { (c, e) -> m.putIfAbsent(c, e) } }
        m
    }

    @Suppress("UNCHECKED_CAST")
    private val emoticons: Map<String, String> get() = (data?.get("emoticons") as? Map<String, String>) ?: emptyMap()
    val quick: List<String> get() = data?.list("quick")?.map { it as String } ?: listOf("👍", "❤️", "😂", "😮", "😢", "🙏")

    fun expand(text: String): String {
        val t = codeRe.replace(text) { codes[it.groupValues[1]] ?: it.value }
        return t.split(" ").joinToString(" ") { emoticons[it] ?: it }
    }
}

object Media {
    data class Picked(val file: File, val name: String, val mime: String, val thumb: ByteArray)

    fun displayName(ctx: Context, uri: Uri): String =
        ctx.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use {
            if (it.moveToFirst()) it.getString(0) else null
        } ?: uri.lastPathSegment ?: "file"

    /** Copy a picked document into cache; recompress big photos (not GIFs). */
    fun prepare(ctx: Context, uri: Uri): Picked {
        val mime = ctx.contentResolver.getType(uri) ?: "application/octet-stream"
        var name = displayName(ctx, uri)
        val dir = File(ctx.cacheDir, "outgoing").apply { mkdirs() }
        var out = File(dir, System.nanoTime().toString() + "_" + name.replace('/', '_'))
        ctx.contentResolver.openInputStream(uri)!!.use { inp -> out.outputStream().use { inp.copyTo(it) } }
        var finalMime = mime
        if (mime.startsWith("image/") && mime != "image/gif") {
            val opts = BitmapFactory.Options().apply { inJustDecodeBounds = true }
            BitmapFactory.decodeFile(out.path, opts)
            if (out.length() > 400_000 || maxOf(opts.outWidth, opts.outHeight) > 1600) {
                val bmp = decodeScaled(out, 1600)
                if (bmp != null) {
                    val jpg = File(dir, out.nameWithoutExtension + ".jpg")
                    jpg.outputStream().use { bmp.compress(Bitmap.CompressFormat.JPEG, 82, it) }
                    out.delete(); out = jpg
                    name = name.substringBeforeLast('.') + ".jpg"
                    finalMime = "image/jpeg"
                }
            }
        }
        val thumb = if (finalMime.startsWith("image/")) thumbnail(out) else ByteArray(0)
        return Picked(out, name, finalMime, thumb)
    }

    fun decodeScaled(f: File, maxDim: Int): Bitmap? {
        val opts = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(f.path, opts)
        var sample = 1
        while (maxOf(opts.outWidth, opts.outHeight) / (sample * 2) >= maxDim) sample *= 2
        val bmp = BitmapFactory.decodeFile(f.path, BitmapFactory.Options().apply { inSampleSize = sample }) ?: return null
        val scale = maxDim.toFloat() / maxOf(bmp.width, bmp.height)
        return if (scale < 1) Bitmap.createScaledBitmap(bmp, (bmp.width * scale).toInt(), (bmp.height * scale).toInt(), true) else bmp
    }

    fun thumbnail(f: File): ByteArray {
        val bmp = decodeScaled(f, 160) ?: return ByteArray(0)
        for (q in intArrayOf(70, 50, 30)) {
            val bo = ByteArrayOutputStream()
            bmp.compress(Bitmap.CompressFormat.JPEG, q, bo)
            if (bo.size() <= 10_000) return bo.toByteArray()
        }
        return ByteArray(0)
    }

    suspend fun loadDrawable(path: String): Drawable? = withContext(Dispatchers.IO) {
        runCatching { ImageDecoder.decodeDrawable(ImageDecoder.createSource(File(path))) { d, info, _ ->
            val max = 1200
            val w = info.size.width; val h = info.size.height
            if (maxOf(w, h) > max) { val s = max.toFloat() / maxOf(w, h); d.setTargetSize((w * s).toInt(), (h * s).toInt()) }
        } }.getOrNull()
    }
}

/** Image or animated GIF from a local file (no Coil; ImageDecoder handles both). */
@Composable
fun MediaImage(path: String, modifier: Modifier = Modifier, animate: Boolean = true) {
    var drawable by remember(path) { mutableStateOf<Drawable?>(null) }
    LaunchedEffect(path) { drawable = Media.loadDrawable(path) }
    AndroidView(
        factory = { ctx -> ImageView(ctx).apply { scaleType = ImageView.ScaleType.FIT_CENTER; adjustViewBounds = true } },
        update = { iv ->
            iv.setImageDrawable(drawable)
            (drawable as? AnimatedImageDrawable)?.let { if (animate) it.start() else it.stop() }
        },
        modifier = modifier,
    )
}
