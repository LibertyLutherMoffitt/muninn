package com.muninn.core

import java.io.InputStream
import java.io.OutputStream
import java.net.InetSocketAddress
import java.net.Socket
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

/** Transport backed by muninn-simhub (python/src/muninn/sim/hub.py). */
class SimTransport(
    hub: String,
    val name: String = "",
    private var addr: String? = null,
    var pos: Pair<Double, Double>? = null,
) : Transport {
    override val maxParallelDials = 2
    private val hubHost = hub.substringBeforeLast(":").ifEmpty { "127.0.0.1" }
    private val hubPort = hub.substringAfterLast(":").toInt()
    @Volatile private var ctrl: Socket? = null
    private val rids = AtomicInteger()
    private val waiting = ConcurrentHashMap<Int, Pair<CountDownLatch, MutableList<Map<String, Any?>>>>()
    @Volatile private var onIncoming: ((Conn, String) -> Unit)? = null
    @Volatile private var stopped = false

    class SockConn(val s: Socket) : Conn {
        override val input: InputStream = s.getInputStream()
        override val output: OutputStream = s.getOutputStream()
        override fun close() = runCatching { s.close() }.let { }
    }

    private fun open(): Socket = Socket().apply {
        tcpNoDelay = true
        connect(InetSocketAddress(hubHost, hubPort), 10_000)
    }

    private fun readLine(s: Socket): Map<String, Any?> {
        val inp = s.getInputStream()
        val buf = java.io.ByteArrayOutputStream()
        while (true) {
            val b = inp.read()
            if (b < 0) throw java.io.EOFException("hub closed")
            if (b == '\n'.code) return Json.obj(buf.toString("UTF-8"))
            buf.write(b)
        }
    }

    private fun send(s: Socket, m: Map<String, Any?>) {
        synchronized(s) { s.getOutputStream().write((Json.write(m) + "\n").toByteArray()); s.getOutputStream().flush() }
    }

    @Synchronized
    private fun register() {
        val s = open()
        val msg = linkedMapOf<String, Any?>("op" to "register", "name" to name, "addr" to addr)
        pos?.let { msg["pos"] = listOf(it.first, it.second) }
        send(s, msg)
        val res = readLine(s)
        addr = res.str("addr")
        ctrl = s
    }

    override fun localAddr(): String {
        if (ctrl == null) register()
        return addr!!
    }

    override fun start(onIncoming: (Conn, String) -> Unit) {
        this.onIncoming = onIncoming
        if (ctrl == null) register()
        Thread({ ctrlLoop() }, "sim-ctrl").apply { isDaemon = true; start() }
    }

    private fun ctrlLoop() {
        while (!stopped) {
            try {
                val s = ctrl ?: register().let { ctrl!! }
                while (true) {
                    val m = readLine(s)
                    when (m.str("op")) {
                        "incoming" -> {
                            val id = m.long("id"); val from = m.str("from")
                            Thread({ accept(id, from) }, "sim-accept").apply { isDaemon = true; start() }
                        }
                        "discovered" -> waiting.remove(m.int("rid"))?.let { (latch, out) ->
                            @Suppress("UNCHECKED_CAST")
                            out.addAll(m.list("devices") as List<Map<String, Any?>>)
                            latch.countDown()
                        }
                    }
                }
            } catch (_: Throwable) {
                if (stopped) return
                ctrl = null
                Thread.sleep(1000)
                runCatching { register() }
            }
        }
    }

    private fun accept(id: Long, from: String) {
        try {
            val s = open()
            send(s, mapOf("op" to "accept", "id" to id))
            if (!readLine(s).bool("ok")) { s.close(); return }
            onIncoming?.invoke(SockConn(s), from)
        } catch (_: Throwable) {}
    }

    override fun stop() {
        stopped = true
        runCatching { ctrl?.close() }
    }

    override fun connect(addr: String): Conn {
        val s = open()
        s.soTimeout = 20_000
        send(s, mapOf("op" to "connect", "from" to localAddr(), "to" to addr))
        val res = try { readLine(s) } catch (e: Throwable) { s.close(); throw java.io.IOException("connect $addr: hub error") }
        if (!res.bool("ok")) { s.close(); throw java.io.IOException("connect $addr: ${res.str("error")}") }
        s.soTimeout = 0
        return SockConn(s)
    }

    override fun scan(durationMs: Long): List<ScanResult> {
        val c = ctrl ?: return emptyList()
        val rid = rids.incrementAndGet()
        val latch = CountDownLatch(1)
        val out = java.util.Collections.synchronizedList(ArrayList<Map<String, Any?>>())
        waiting[rid] = latch to out
        send(c, mapOf("op" to "discover", "rid" to rid))
        latch.await(maxOf(durationMs * 3, 15_000), TimeUnit.MILLISECONDS)
        return out.map { ScanResult(it.str("addr"), it.str("name"), it.bool("muninn"), it.int("rssi")) }
    }
}
