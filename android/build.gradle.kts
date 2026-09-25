plugins {
    id("com.android.application") version "8.7.3" apply false
    id("org.jetbrains.kotlin.android") version "2.0.21" apply false
    id("org.jetbrains.kotlin.plugin.compose") version "2.0.21" apply false
    // Renders Compose screens on the JVM with Android's own layout engine, so
    // the UI can be looked at (and diffed) with no emulator or device.
    id("app.cash.paparazzi") version "1.3.5" apply false
}
