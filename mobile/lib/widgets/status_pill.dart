import 'package:flutter/material.dart';

import '../core/motion.dart';
import '../core/tokens.dart';

/// A small status pill with icon + label. Used for connection state (Offline •
/// Local AI), mode indicators, and status badges. Colour is conveyed by BOTH
/// the icon and the text (WCAG 1.4.1). Clean rounded pill with soft colored
/// background matching the reference design.
class StatusPill extends StatelessWidget {
  const StatusPill({
    super.key,
    required this.label,
    required this.color,
    this.icon,
    this.semanticLabel,
  });

  final String label;
  final Color color;
  final IconData? icon;
  final String? semanticLabel;

  @override
  Widget build(BuildContext context) {
    // Derive a soft background from the color
    final bgColor = _softBg(color);
    final textColor = _darkVariant(color);

    return Semantics(
      label: semanticLabel ?? label,
      container: true,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: T.s12, vertical: T.s6),
        decoration: BoxDecoration(
          color: bgColor,
          borderRadius: BorderRadius.circular(T.rPill),
          border: Border.all(color: color.withValues(alpha: 0.2)),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            if (icon != null)
              Icon(icon, size: 15, color: textColor)
            else
              _BreathingDot(color: color),
            const SizedBox(width: T.s6),
            Flexible(
              child: Text(
                label,
                overflow: TextOverflow.ellipsis,
                style: Theme.of(context).textTheme.labelMedium?.copyWith(
                      color: textColor,
                      fontWeight: FontWeight.w600,
                      fontSize: 12,
                    ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Color _softBg(Color c) {
    if (c == T.danger) return T.dangerBg;
    if (c == T.known || c == T.success) return T.knownBg;
    if (c == T.unknown || c == T.accent) return T.accentBg;
    if (c == T.primary) return T.primaryLighter;
    return c.withValues(alpha: 0.1);
  }

  Color _darkVariant(Color c) {
    if (c == T.danger) return const Color(0xFFB91C1C);
    if (c == T.known || c == T.success) return const Color(0xFF15803D);
    if (c == T.unknown || c == T.accent) return const Color(0xFFB45309);
    if (c == T.primary) return T.primary;
    return c;
  }
}

/// The pill's glowing dot: a slow opacity breath. Still under reduce-motion.
class _BreathingDot extends StatefulWidget {
  const _BreathingDot({required this.color});

  final Color color;

  @override
  State<_BreathingDot> createState() => _BreathingDotState();
}

class _BreathingDotState extends State<_BreathingDot>
    with SingleTickerProviderStateMixin {
  AnimationController? _c;

  void _sync(bool still) {
    if (still) {
      _c?.stop();
      return;
    }
    _c ??= AnimationController(vsync: this, duration: T.xslow * 2);
    if (!_c!.isAnimating) _c!.repeat(reverse: true);
  }

  @override
  void dispose() {
    _c?.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final still = context.reduceMotion;
    _sync(still);
    final dot = Container(
      width: 8,
      height: 8,
      decoration: BoxDecoration(
        color: widget.color,
        shape: BoxShape.circle,
      ),
    );
    final c = _c;
    if (still || c == null) return dot;
    return FadeTransition(
      opacity: Tween(begin: 0.55, end: 1.0)
          .animate(CurvedAnimation(parent: c, curve: Curves.easeInOut)),
      child: dot,
    );
  }
}

/// Maps a module `state` string to a semantic colour.
Color stateColor(String state) => switch (state) {
      'ok' => T.success,
      'placeholder' => T.accent,
      'unavailable' => T.danger,
      _ => const Color(0xFF8A8F98), // off / unknown — neutral grey
    };
