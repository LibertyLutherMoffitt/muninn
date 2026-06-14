package com.muninn

import android.os.Build
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.dynamicDarkColorScheme
import androidx.compose.material3.dynamicLightColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp

// "Raven" palette — near-black surfaces, soft lavender accent. Quiet and
// elegant so the conversation, not the chrome, carries the screen.
private val DarkColors = darkColorScheme(
    primary = Color(0xFFB7A6FF),
    onPrimary = Color(0xFF221152),
    primaryContainer = Color(0xFF34245C), // own message bubble
    onPrimaryContainer = Color(0xFFE9DEFF),
    secondary = Color(0xFFC9C5DD),
    onSecondary = Color(0xFF302D41),
    background = Color(0xFF0D0D10),
    onBackground = Color(0xFFE7E6EC),
    surface = Color(0xFF131318),
    onSurface = Color(0xFFE7E6EC),
    surfaceVariant = Color(0xFF232027), // incoming message bubble
    onSurfaceVariant = Color(0xFFC9C5D0),
    outline = Color(0xFF49454F),
    outlineVariant = Color(0xFF2A2730),
    error = Color(0xFFFFB4AB),
)

private val LightColors = lightColorScheme(
    primary = Color(0xFF5A43D6),
    onPrimary = Color(0xFFFFFFFF),
    primaryContainer = Color(0xFFE7DEFF), // own message bubble
    onPrimaryContainer = Color(0xFF1B0A57),
    secondary = Color(0xFF5E5A71),
    onSecondary = Color(0xFFFFFFFF),
    background = Color(0xFFFBF8FF),
    onBackground = Color(0xFF1B1B1F),
    surface = Color(0xFFFFFFFF),
    onSurface = Color(0xFF1B1B1F),
    surfaceVariant = Color(0xFFECE8F1), // incoming message bubble
    onSurfaceVariant = Color(0xFF49454E),
    outline = Color(0xFF7A757F),
    outlineVariant = Color(0xFFD7D2DC),
    error = Color(0xFFBA1A1A),
)

private val MuninnTypography = Typography().run {
    val display = FontFamily.SansSerif
    copy(
        titleLarge = titleLarge.copy(
            fontFamily = display,
            fontWeight = FontWeight.SemiBold,
            fontSize = 22.sp,
            letterSpacing = 0.sp,
        ),
        bodyMedium = bodyMedium.copy(fontSize = 15.sp, lineHeight = 21.sp),
        labelSmall = labelSmall.copy(letterSpacing = 0.4.sp),
    )
}

@Composable
fun MuninnTheme(content: @Composable () -> Unit) {
    val dark = isSystemInDarkTheme()
    val context = LocalContext.current
    val colors = when {
        // Material You on Android 12+ — blends with the user's wallpaper.
        Build.VERSION.SDK_INT >= Build.VERSION_CODES.S ->
            if (dark) dynamicDarkColorScheme(context) else dynamicLightColorScheme(context)
        dark -> DarkColors
        else -> LightColors
    }
    MaterialTheme(colorScheme = colors, typography = MuninnTypography, content = content)
}
