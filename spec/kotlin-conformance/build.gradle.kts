// Standalone JVM harness that runs the Kotlin client's wire code against
// spec/wire-vectors.json — the same fixture the Python suite checks.
//
// Why a separate project rather than an androidTest: Protocol.kt is pure JVM
// (java.io / java.nio / java.util only), so it can be compiled and tested with
// no Android SDK installed. That keeps cross-client conformance verifiable in
// CI and on any dev machine, not just ones with a full Android toolchain.
plugins {
    kotlin("jvm") version "2.0.21"
}

sourceSets {
    main {
        kotlin.setSrcDirs(listOf("../../android/app/src/main/kotlin"))
        // Everything else in that tree pulls in android.* and cannot compile here.
        kotlin.include("com/muninn/Protocol.kt")
    }
}

dependencies {
    testImplementation(kotlin("test"))
    testImplementation("org.json:json:20240303")
    // lazysodium-java is the desktop twin of the app's lazysodium-android and
    // wraps the identical libsodium primitives, so a crypto vector that passes
    // here is the same construction the phone performs.
    testImplementation("com.goterl:lazysodium-java:5.1.4")
    testImplementation("net.java.dev.jna:jna:5.14.0")
}

tasks.test {
    useJUnitPlatform()
    testLogging { events("passed", "failed", "skipped") }
}
