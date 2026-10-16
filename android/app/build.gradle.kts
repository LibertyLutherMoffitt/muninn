plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

// One version for every client: read it from python/pyproject.toml.
val muninnVersion: String = Regex("""(?m)^version\s*=\s*"([^"]+)"""")
    .find(rootProject.file("../python/pyproject.toml").readText())!!.groupValues[1]
val muninnVersionCode: Int = muninnVersion.split(".").map { it.takeWhile(Char::isDigit).toInt() }
    .let { (a, b, c) -> a * 10000 + b * 100 + c }

// All builds are signed with the Muninn key when it's present, so an APK
// built on any machine (or by CI) can update an installed one in place.
// Not in git: ~/.config/muninn/android-signing.keystore (or $MUNINN_KEYSTORE).
val muninnKeystore = file(System.getenv("MUNINN_KEYSTORE")
    ?: "${System.getProperty("user.home")}/.config/muninn/android-signing.keystore")

android {
    namespace = "com.muninn"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.muninn"
        minSdk = 31
        targetSdk = 34
        versionCode = muninnVersionCode
        versionName = muninnVersion
        buildConfigField("String", "UPDATE_REPO", "\"LibertyLutherMoffitt/muninn\"")

    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    signingConfigs {
        if (muninnKeystore.exists()) create("muninn") {
            storeFile = muninnKeystore
            storePassword = System.getenv("MUNINN_KEYSTORE_PASSWORD") ?: "android"
            keyAlias = System.getenv("MUNINN_KEY_ALIAS") ?: "androiddebugkey"
            keyPassword = System.getenv("MUNINN_KEY_PASSWORD") ?: "android"
        }
    }

    // Games, stickers and emoji data are shared with the desktop client and
    // live in the Python package; pack them as assets.
    sourceSets["main"].assets.srcDirs("src/main/assets", layout.buildDirectory.dir("generated/muninnAssets"))

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    buildTypes {
        val signing = signingConfigs.findByName("muninn")
        debug { if (signing != null) signingConfig = signing }
        release {
            isMinifyEnabled = false
            signingConfig = signing ?: signingConfigs.getByName("debug")
        }
    }

    packaging {
        resources {
            excludes += "/META-INF/{AL2.0,LGPL2.1}"
        }
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.7")
    implementation("androidx.lifecycle:lifecycle-service:2.8.7")
    implementation("androidx.activity:activity-compose:1.9.3")
    implementation(platform("androidx.compose:compose-bom:2024.12.01"))
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-graphics")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-core")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")

    implementation(project(":core"))
}

val muninnAssets by tasks.registering(Copy::class) {
    val shared = rootDir.resolve("../python/src/muninn")
    from(shared.resolve("games")) { include("*.js"); into("games") }
    from(shared.resolve("assets")) { include("emoji.json", "stickers/*.gif") }
    into(layout.buildDirectory.dir("generated/muninnAssets"))
}
tasks.named("preBuild") { dependsOn(muninnAssets) }
