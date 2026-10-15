plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

android {
    namespace = "com.muninn"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.muninn"
        minSdk = 31
        targetSdk = 34
        versionCode = 2
        versionName = "0.2.0"

    }

    buildFeatures {
        compose = true
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
        release {
            isMinifyEnabled = false
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
