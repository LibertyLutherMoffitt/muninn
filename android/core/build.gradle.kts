plugins {
    id("org.jetbrains.kotlin.jvm")
    application
}

kotlin {
    jvmToolchain(17)
}

application {
    // JVM sim node: joins muninn-simhub for cross-language interop tests.
    mainClass.set("com.muninn.core.SimNodeKt")
}

dependencies {
    testImplementation(kotlin("test"))
}

tasks.test {
    useJUnitPlatform()
    testLogging { events("failed"); showStandardStreams = false }
}
