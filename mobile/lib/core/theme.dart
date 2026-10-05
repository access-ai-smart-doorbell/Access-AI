import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';

import 'tokens.dart';

/// Which theme the user has chosen. High-contrast is a distinct, WCAG-AAA-leaning
/// scheme rather than a tweak on dark, so it can be validated independently.
enum AppThemeChoice { system, light, dark, highContrast }

extension AppThemeChoiceLabel on AppThemeChoice {
  String get label => switch (this) {
        AppThemeChoice.system => 'Follow system',
        AppThemeChoice.light => 'Light',
        AppThemeChoice.dark => 'Dark',
        AppThemeChoice.highContrast => 'High contrast',
      };

  String get id => name;

  static AppThemeChoice fromId(String? id) =>
      AppThemeChoice.values.firstWhere((e) => e.name == id,
          orElse: () => AppThemeChoice.system);
}

/// AccessAI bright, accessible theming. Light is the hero: bright white canvas,
/// electric blue primary, dark navy text, Inter font throughout. Clean,
/// premium, accessibility-focused design matching the reference mockups.
class AppTheme {
  AppTheme._();

  static ThemeData light() => _base(_lightScheme(), Brightness.light);

  static ThemeData dark() => _base(_darkScheme(), Brightness.dark);

  static ColorScheme _lightScheme() => ColorScheme.fromSeed(
        seedColor: T.primary,
        brightness: Brightness.light,
      ).copyWith(
        primary: T.primary,
        onPrimary: Colors.white,
        primaryContainer: T.primaryLight,
        onPrimaryContainer: T.textPrimary,
        secondary: T.known,
        onSecondary: Colors.white,
        tertiary: T.multi,
        onTertiary: Colors.white,
        error: T.danger,
        onError: Colors.white,
        surface: T.bg,
        onSurface: T.textPrimary,
        onSurfaceVariant: T.textSecondary,
        surfaceContainerLowest: Colors.white,
        surfaceContainerLow: const Color(0xFFF8FAFC),
        surfaceContainer: T.bgCard,
        surfaceContainerHigh: T.bgSubtle,
        surfaceContainerHighest: const Color(0xFFE8ECF1),
        surfaceTint: Colors.transparent,
        outline: T.border,
        outlineVariant: T.borderLight,
        inverseSurface: T.textPrimary,
        onInverseSurface: Colors.white,
      );

  static ColorScheme _darkScheme() =>
      ColorScheme.fromSeed(seedColor: T.primary, brightness: Brightness.dark)
          .copyWith(
        primary: T.primary,
        onPrimary: Colors.white,
        primaryContainer: const Color(0xFF14532D),
        onPrimaryContainer: const Color(0xFFBBF7D0),
        secondary: T.known,
        onSecondary: Colors.white,
        tertiary: T.multi,
        onTertiary: Colors.white,
        error: T.danger,
        onError: Colors.white,
        surface: const Color(0xFF0F172A),
        onSurface: const Color(0xFFF8FAFC),
        onSurfaceVariant: const Color(0xFF94A3B8),
        surfaceContainerLowest: const Color(0xFF0B1120),
        surfaceContainerLow: const Color(0xFF131C2E),
        surfaceContainer: const Color(0xFF1E293B),
        surfaceContainerHigh: const Color(0xFF273449),
        surfaceContainerHighest: const Color(0xFF2E3D54),
        surfaceTint: Colors.transparent,
        outline: const Color(0xFF334155),
        outlineVariant: const Color(0xFF22304A),
        inverseSurface: const Color(0xFFF8FAFC),
        onInverseSurface: const Color(0xFF0B1120),
      );

  /// Maximised contrast: near-black surfaces, pure-white text, a bright accent.
  static ThemeData highContrast() {
    const cs = ColorScheme(
      brightness: Brightness.dark,
      primary: Color(0xFFFFE600),
      onPrimary: Color(0xFF000000),
      secondary: Color(0xFF00E5FF),
      onSecondary: Color(0xFF000000),
      error: Color(0xFFFF5252),
      onError: Color(0xFF000000),
      surface: Color(0xFF000000),
      onSurface: Color(0xFFFFFFFF),
      surfaceContainerHighest: Color(0xFF1A1A1A),
      outline: Color(0xFFFFFFFF),
    );
    return _base(cs, Brightness.dark, highContrast: true);
  }

  static ThemeData _base(ColorScheme cs, Brightness brightness,
      {bool highContrast = false}) {
    final baseText = brightness == Brightness.dark
        ? Typography.material2021().white
        : Typography.material2021().black;
    // Inter everywhere — clean geometric sans-serif, highly readable.
    final textTheme = GoogleFonts.interTextTheme(baseText).copyWith(
      displayLarge: GoogleFonts.inter(
          textStyle: baseText.displayLarge,
          fontWeight: FontWeight.w800,
          letterSpacing: -1.2,
          color: T.textPrimary),
      displayMedium: GoogleFonts.inter(
          textStyle: baseText.displayMedium,
          fontWeight: FontWeight.w800,
          letterSpacing: -0.8,
          color: T.textPrimary),
      displaySmall: GoogleFonts.inter(
          textStyle: baseText.displaySmall,
          fontWeight: FontWeight.w700,
          letterSpacing: -0.5,
          color: T.textPrimary),
      headlineLarge: GoogleFonts.inter(
          textStyle: baseText.headlineLarge,
          fontWeight: FontWeight.w700,
          letterSpacing: -0.5,
          color: T.textPrimary),
      headlineMedium: GoogleFonts.inter(
          textStyle: baseText.headlineMedium,
          fontWeight: FontWeight.w700,
          letterSpacing: -0.3,
          color: T.textPrimary),
      headlineSmall: GoogleFonts.inter(
          textStyle: baseText.headlineSmall,
          fontWeight: FontWeight.w700,
          color: T.textPrimary),
      titleLarge: GoogleFonts.inter(
          textStyle: baseText.titleLarge,
          fontWeight: FontWeight.w700,
          letterSpacing: -0.2,
          color: T.textPrimary),
      titleMedium: GoogleFonts.inter(
          textStyle: baseText.titleMedium,
          fontWeight: FontWeight.w600,
          color: T.textPrimary),
      titleSmall: GoogleFonts.inter(
          textStyle: baseText.titleSmall,
          fontWeight: FontWeight.w600,
          color: T.textSecondary),
      bodyLarge: GoogleFonts.inter(
          textStyle: baseText.bodyLarge,
          fontWeight: FontWeight.w400,
          color: T.textPrimary),
      bodyMedium: GoogleFonts.inter(
          textStyle: baseText.bodyMedium,
          fontWeight: FontWeight.w400,
          color: T.textSecondary),
      bodySmall: GoogleFonts.inter(
          textStyle: baseText.bodySmall,
          fontWeight: FontWeight.w400,
          color: T.textTertiary),
      labelLarge: GoogleFonts.inter(
          textStyle: baseText.labelLarge,
          fontWeight: FontWeight.w600,
          color: T.textPrimary),
      labelMedium: GoogleFonts.inter(
          textStyle: baseText.labelMedium,
          fontWeight: FontWeight.w500,
          color: T.textSecondary),
      labelSmall: GoogleFonts.inter(
          textStyle: baseText.labelSmall,
          fontWeight: FontWeight.w500,
          color: T.textTertiary),
    );

    return ThemeData(
      useMaterial3: true,
      colorScheme: cs,
      scaffoldBackgroundColor: highContrast ? cs.surface : T.bg,
      textTheme: textTheme,
      splashFactory: InkSparkle.splashFactory,
      visualDensity: VisualDensity.comfortable,
      appBarTheme: AppBarTheme(
        backgroundColor: Colors.transparent,
        elevation: 0,
        scrolledUnderElevation: 0,
        centerTitle: false,
        foregroundColor: T.textPrimary,
        titleTextStyle: textTheme.titleLarge?.copyWith(color: T.textPrimary),
      ),
      cardTheme: CardThemeData(
        elevation: highContrast ? 0 : 0,
        color: T.bgCard,
        surfaceTintColor: Colors.transparent,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(T.rLg),
          side: highContrast
              ? BorderSide(color: cs.outline, width: 2)
              : const BorderSide(color: T.border, width: 1),
        ),
      ),
      dividerTheme: DividerThemeData(
        color: highContrast ? cs.outline : T.border,
        thickness: 1,
        space: T.s24,
      ),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          minimumSize: const Size(T.minTouch, T.minTouch),
          padding:
              const EdgeInsets.symmetric(horizontal: T.s24, vertical: T.s16),
          shape:
              RoundedRectangleBorder(borderRadius: BorderRadius.circular(T.rMd)),
          textStyle: textTheme.titleMedium,
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          minimumSize: const Size(T.minTouch, T.minTouch),
          side: BorderSide(
              color: highContrast ? cs.outline : T.border,
              width: highContrast ? 2 : 1),
          shape:
              RoundedRectangleBorder(borderRadius: BorderRadius.circular(T.rMd)),
        ),
      ),
      textButtonTheme: TextButtonThemeData(
        style: TextButton.styleFrom(
          minimumSize: const Size(T.minTouch, 48),
          shape:
              RoundedRectangleBorder(borderRadius: BorderRadius.circular(T.rSm)),
        ),
      ),
      iconButtonTheme: IconButtonThemeData(
        style: IconButton.styleFrom(
          minimumSize: const Size(T.minTouch, T.minTouch),
        ),
      ),
      chipTheme: ChipThemeData(
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(T.rPill),
          side: BorderSide(
              color: highContrast ? cs.outline : T.border, width: 1),
        ),
        backgroundColor: highContrast ? cs.surface : T.bgSubtle,
        labelStyle: textTheme.labelLarge?.copyWith(color: T.textPrimary),
        padding: const EdgeInsets.symmetric(horizontal: T.s12, vertical: T.s10),
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: highContrast ? cs.surfaceContainerHighest : T.bgSubtle,
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(T.rMd),
          borderSide: BorderSide(
              color: highContrast ? cs.outline : T.border),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(T.rMd),
          borderSide: BorderSide(
              color: highContrast ? cs.outline : T.border),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(T.rMd),
          borderSide: BorderSide(color: cs.primary, width: 2),
        ),
      ),
      snackBarTheme: SnackBarThemeData(
        behavior: SnackBarBehavior.floating,
        backgroundColor: highContrast ? cs.surfaceContainerHighest : null,
        shape:
            RoundedRectangleBorder(borderRadius: BorderRadius.circular(T.rSm)),
      ),
      navigationBarTheme: NavigationBarThemeData(
        height: 76,
        backgroundColor: Colors.transparent,
        indicatorColor: T.primaryLight,
        surfaceTintColor: Colors.transparent,
        shadowColor: Colors.transparent,
        labelTextStyle: WidgetStatePropertyAll(
            textTheme.labelMedium?.copyWith(fontWeight: FontWeight.w600)),
      ),
      switchTheme: SwitchThemeData(
        thumbColor: WidgetStateProperty.resolveWith((states) =>
            states.contains(WidgetState.selected) ? cs.onPrimary : null),
        trackColor: WidgetStateProperty.resolveWith((states) =>
            states.contains(WidgetState.selected) ? cs.primary : null),
      ),
      pageTransitionsTheme: const PageTransitionsTheme(builders: {
        TargetPlatform.android: FadeForwardsPageTransitionsBuilder(),
      }),
    );
  }
}
