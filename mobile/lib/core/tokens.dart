import 'package:flutter/material.dart';

/// Design tokens for AccessAI — the bright, accessible light design system.
///
/// One source of truth for spacing, radii, motion and the signature palette.
/// An 8pt spacing scale keeps rhythm consistent. The canvas is bright white /
/// extremely light blue-gray. The primary is an accessible electric blue.
/// Semantic colours are soft: green for known, amber/orange for unknown,
/// coral/red for offline, purple for multi-person.
///
/// The token *names* are the app's public contract — screens reference `T.s16`,
/// `T.rMd`, `T.primary`, `T.known`, `T.unknown`, `T.minTouch`.
/// Values evolve; names stay stable.
class T {
  T._();

  // ---- 8pt spacing scale ----
  static const double s2 = 2;
  static const double s4 = 4;
  static const double s6 = 6;
  static const double s8 = 8;
  static const double s10 = 10;
  static const double s12 = 12;
  static const double s16 = 16;
  static const double s20 = 20;
  static const double s24 = 24;
  static const double s32 = 32;
  static const double s40 = 40;
  static const double s48 = 48;
  static const double s56 = 56;
  static const double s64 = 64;

  // ---- Corner radii (generous, premium, soft) ----
  static const double rSm = 14;
  static const double rMd = 20;
  static const double rLg = 24;
  static const double rXl = 32;
  static const double rPill = 999;

  // ---- Touch target — accessibility hard floor (comfortably above 48dp) ----
  static const double minTouch = 56;

  // ---- Bright accessible palette ----
  // Backgrounds
  static const Color bg = Color(0xFFF7F9FC);        // app canvas (lightest)
  static const Color bgCard = Color(0xFFFFFFFF);     // card surfaces
  static const Color bgSubtle = Color(0xFFF0F4F8);   // nested sections

  // Text
  static const Color textPrimary = Color(0xFF1A2138);   // dark navy headings
  static const Color textSecondary = Color(0xFF5A6478); // body/supporting
  static const Color textTertiary = Color(0xFF8E95A5);  // hints/timestamps

  // Borders & shadows
  static const Color border = Color(0xFFE4E8EE);     // card borders
  static const Color borderLight = Color(0xFFF0F2F5); // subtle borders
  static const Color shadow = Color(0x0F1A2138);      // soft card shadow

  // ---- Primary accent — electric blue ----
  static const Color primary = Color(0xFF2563EB);      // buttons, active nav
  static const Color primaryLight = Color(0xFFDBEAFE);  // soft blue surfaces
  static const Color primaryLighter = Color(0xFFEFF6FF); // very subtle blue bg

  // ---- Semantic colors ----
  // Known / success
  static const Color known = Color(0xFF16A34A);         // green icons/text
  static const Color knownBg = Color(0xFFDCFCE7);       // soft green background
  static const Color knownBorder = Color(0xFFBBF7D0);   // green border

  // Unknown / warning
  static const Color unknown = Color(0xFFF97316);        // orange icons/text
  static const Color unknownBg = Color(0xFFFFF7ED);      // soft amber background
  static const Color unknownBorder = Color(0xFFFED7AA);  // orange border

  // Offline / error
  static const Color danger = Color(0xFFEF4444);         // red icons/text
  static const Color dangerBg = Color(0xFFFEF2F2);       // soft coral background
  static const Color dangerBorder = Color(0xFFFECACA);   // red border

  // Multi-person
  static const Color multi = Color(0xFF7C3AED);          // purple icons
  static const Color multiBg = Color(0xFFF3E8FF);        // soft purple bg
  static const Color multiBorder = Color(0xFFE9D5FF);    // purple border

  // Ring/doorbell
  static const Color accent = Color(0xFFF59E0B);         // amber doorbell
  static const Color accentBg = Color(0xFFFFFBEB);       // soft amber bg
  static const Color accentBorder = Color(0xFFFDE68A);   // amber border

  // Hear visitor
  static const Color hearBg = Color(0xFFEFF6FF);         // soft blue bg

  // Legacy aliases for compatibility
  static const Color seed = Color(0xFF2563EB);
  static const Color success = Color(0xFF16A34A);

  // Legacy dark-theme aliases — kept so unchanged screens compile.
  static const Color bg2 = bg;
  static const Color surface = bgCard;
  static const Color surfaceHi = bgSubtle;
  static const Color fg = textPrimary;
  static const Color muted = textTertiary;
  static const Color faint = textTertiary;
  static const Color hairline = border;
  static const Color jarvis1 = Color(0xFF38BDF8);
  static const Color jarvis2 = Color(0xFF818CF8);
  static const Color jarvis3 = Color(0xFFC084FC);
  static const Color mesh1 = primary;
  static const Color mesh2 = Color(0xFF38BDF8);
  static const Color mesh3 = Color(0xFF818CF8);
  static const Color deafFlash = accent;
  static const LinearGradient aurora = LinearGradient(
    begin: Alignment.topLeft,
    end: Alignment.bottomRight,
    colors: [Color(0xFF38BDF8), Color(0xFF818CF8), Color(0xFFC084FC)],
  );
  static const LinearGradient greenGlow = LinearGradient(
    begin: Alignment.topLeft,
    end: Alignment.bottomRight,
    colors: [Color(0xFF34D399), Color(0xFF16A34A)],
  );

  // ---- Motion ----
  static const Duration instant = Duration(milliseconds: 90);
  static const Duration fast = Duration(milliseconds: 180);
  static const Duration med = Duration(milliseconds: 340);
  static const Duration slow = Duration(milliseconds: 620);
  static const Duration xslow = Duration(milliseconds: 1100);

  // Signature curves.
  static const Cubic easeExpo = Cubic(0.16, 1.0, 0.3, 1.0);
  static const Cubic easeEmphasized = Cubic(0.2, 0.0, 0.0, 1.0);
  static const Cubic springy = Cubic(0.34, 1.56, 0.64, 1.0);
}
