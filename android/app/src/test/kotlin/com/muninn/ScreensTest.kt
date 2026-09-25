package com.muninn

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import app.cash.paparazzi.DeviceConfig
import app.cash.paparazzi.Paparazzi
import com.muninn.ui.ChatScreen
import com.muninn.ui.ConversationListScreen
import com.muninn.ui.NewGroupDialog
import com.muninn.ui.PeersSheet
import org.junit.After
import org.junit.Rule
import org.junit.Test

/**
 * Renders the app's screens on the JVM with Android's own layout engine
 * (Paparazzi), seeded with a realistic cabin: someone connected, someone
 * reachable only through them, someone visible who won't connect, someone
 * who has left, and a group spanning all of them.
 *
 *     gradle :app:recordPaparazziDebug
 *
 * writes PNGs to app/src/test/snapshots/images/. This is how the UI gets
 * looked at without an emulator; it does not replace trying it on a phone.
 */
class ScreensTest {

    @get:Rule
    val paparazzi = Paparazzi(
        deviceConfig = DeviceConfig.PIXEL_5,
        theme = "android:Theme.Material.NoActionBar",
    )

    private val me = "02:AB:CD:EF:01:23"
    private val ravn = "AA:BB:CC:11:22:33" // a laptop, connected
    private val hugin = "02:44:55:66:77:88" // a phone, two rows back via Ravn
    private val sam = "CC:DD:EE:11:22:44" // visible, won't connect
    private val jo = "DD:EE:FF:00:11:22" // went to the galley
    private val group = ByteArray(16) { (0x40 + it).toByte() }
    private lateinit var mesh: Mesh

    private object Plain : Sealer {
        override fun seal(plaintext: ByteArray, theirPub: ByteArray) = plaintext
        override fun open(sealed: ByteArray, theirPub: ByteArray) = sealed
    }

    private fun seedCabin(empty: Boolean = false) {
        val store = MemoryMeshStore()
        val now = System.currentTimeMillis() / 1000
        if (!empty) {
            for ((id, name) in listOf(ravn to "Ravn", hugin to "Hugin", sam to "Sam's laptop", jo to "Jo")) {
                store.savePeerKey(id, ByteArray(32) { id.hashCode().toByte() }, direct = true)
                store.savePeerName(id, name)
            }
            store.saveGroup(
                MeshGroup(
                    group,
                    "Row 14",
                    mapOf(me to ByteArray(32), ravn to ByteArray(32), hugin to ByteArray(32), jo to ByteArray(32)),
                ),
            )
            var n = 0
            fun id() = ByteArray(16) { (++n).toByte() }
            fun incoming(from: String, text: String, ago: Long, g: ByteArray = ZERO_GROUP_ID, seen: Boolean = true) {
                val msgId = id()
                store.saveIncoming(StoredMessage(msgId, g, from, text, now - ago))
                if (seen) store.markDisplayed(listOf(msgId))
            }
            fun outgoing(to: List<String>, text: String, ago: Long, g: ByteArray = ZERO_GROUP_ID, acked: List<String> = emptyList(), read: List<String> = emptyList()) {
                val msgId = id()
                store.saveOutgoing(StoredMessage(msgId, g, me, text, now - ago), to)
                acked.forEach { store.markAcked(msgId, it) }
                read.forEach { store.markRead(msgId, it) }
            }
            incoming(ravn, "Are you on the 14:05 to Oslo too?", 1500)
            outgoing(listOf(ravn), "Yes! Row 14, window seat", 1440, acked = listOf(ravn), read = listOf(ravn))
            incoming(ravn, "Ha, I'm in 12C. No wifi on this one", 1400)
            outgoing(listOf(ravn), "That's what this is for 😄", 1380, acked = listOf(ravn))
            outgoing(listOf(jo), "Grab me a coffee while you're up?", 300)
            incoming(hugin, "Can you see the fjords from your side?", 900, seen = false)
            incoming(ravn, "Anyone want to split a taxi at Gardermoen?", 600, group)
            incoming(hugin, "Yes please", 560, group)
            outgoing(listOf(ravn, hugin, jo), "Count me in", 500, group, acked = listOf(ravn, hugin))
            incoming(ravn, "Meet at the exit by baggage claim", 120, group, seen = false)
        }
        mesh = Mesh(me, ByteArray(32), Plain, store)
        if (!empty) {
            mesh.book.recordConnected(ravn)
            mesh.book.recordRelay(hugin, ravn, hops = 2)
            mesh.book.recordSighting(sam, rssi = -70, muninn = true)
            repeat(PeerBook.UNREACHABLE_AFTER) { mesh.book.recordDialFailure(sam, "page timeout") }
            mesh.book.recordSighting(jo, now = System.currentTimeMillis() - 40 * 60_000L, muninn = true)
        }
        ChatRepository.attach(mesh, store)
    }

    @After
    fun tearDown() {
        mesh.close()
    }

    private fun shoot(dark: Boolean = true, content: @Composable () -> Unit) {
        paparazzi.snapshot {
            MuninnTheme(dark = dark, dynamic = false) {
                Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) { content() }
            }
        }
    }

    private val noop: () -> Unit = {}

    @Composable
    private fun List() = ConversationListScreen(
        onOpen = {},
        onNewGroup = noop,
        onShowPeers = noop,
        onPair = noop,
        onScanMode = noop,
        onYourName = noop,
        scanPolicy = ScanPolicy.AGGRESSIVE,
    )

    @Test
    fun conversations() {
        seedCabin()
        shoot { List() }
    }

    @Test
    fun conversationsLight() {
        seedCabin()
        shoot(dark = false) { List() }
    }

    @Test
    fun conversationsEmpty() {
        seedCabin(empty = true)
        shoot { List() }
    }

    @Test
    fun chatConnected() {
        seedCabin()
        shoot { ChatScreen(ChatRepository.dmId(ravn), onBack = noop) }
    }

    @Test
    fun chatThroughARelay() {
        seedCabin()
        shoot { ChatScreen(ChatRepository.dmId(hugin), onBack = noop) }
    }

    @Test
    fun chatOutOfReach() {
        seedCabin()
        shoot { ChatScreen(ChatRepository.dmId(jo), onBack = noop) }
    }

    @Test
    fun chatGroup() {
        seedCabin()
        shoot { ChatScreen(ChatRepository.groupConvId(group), onBack = noop) }
    }

    @Test
    fun chatGroupLight() {
        seedCabin()
        shoot(dark = false) { ChatScreen(ChatRepository.groupConvId(group), onBack = noop) }
    }

    @Test
    fun nearby() {
        seedCabin()
        shoot { Box(Modifier.fillMaxSize()) { PeersSheet(onOpen = {}) } }
    }

    @Test
    fun newGroup() {
        seedCabin()
        shoot { Box(Modifier.fillMaxSize()) { NewGroupDialog(onDismiss = noop, onCreate = { _, _ -> }) } }
    }
}
