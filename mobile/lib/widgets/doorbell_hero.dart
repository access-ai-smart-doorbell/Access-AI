import 'package:flutter/material.dart';

import '../core/motion.dart';
import '../core/tokens.dart';

/// The signature animated smart-doorbell hero. Warm amber/gold concentric
/// rings pulse outward from a glowing bell disc, evoking a ring in progress.
/// Matches the reference design: warm amber glow on a light background.
/// Fully self-contained (custom-painted, no external asset).
/// Motion-safe: when reduce-motion is on it renders a single static ring +
/// bell with no animation.
class DoorbellHero extends StatefulWidget {
  const DoorbellHero({
    super.key,
    this.size = 220,
    this.active = false,
    this.semanticLabel = 'AccessAI doorbell',
  });

  /// [active] pulses faster/brighter (e.g. while an event is fresh).
  final double size;
  final bool active;
  final String semanticLabel;

  @override
  State<DoorbellHero> createState() => _DoorbellHeroState();
}

class _DoorbellHeroState extends State<DoorbellHero>
    with SingleTickerProviderStateMixin {
  late final AnimationController _c;

  @override
  void initState() {
    super.initState();
    _c = AnimationController(
      vsync: this,
      duration: const Duration(seconds: 3),
    );
  }

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final reduce = context.reduceMotion;

    // Drive/stop the loop based on motion preference.
    if (reduce) {
      if (_c.isAnimating) _c.stop();
    } else {
      if (!_c.isAnimating) _c.repeat();
    }

    return Semantics(
      label: widget.semanticLabel,
      image: true,
      child: SizedBox(
        width: widget.size,
        height: widget.size,
        child: AnimatedBuilder(
          animation: _c,
          builder: (context, _) {
            return CustomPaint(
              painter: _DoorbellPainter(
                t: reduce ? 0.0 : _c.value,
                active: widget.active,
                animate: !reduce,
              ),
            );
          },
        ),
      ),
    );
  }
}

class _DoorbellPainter extends CustomPainter {
  _DoorbellPainter({
    required this.t,
    required this.active,
    required this.animate,
  });

  final double t; // 0..1 loop phase
  final bool active;
  final bool animate;

  @override
  void paint(Canvas canvas, Size size) {
    final center = Offset(size.width / 2, size.height / 2);
    final maxR = size.width / 2;

    // Warm amber pulsing rings — reference shows golden glow
    final waves = animate ? 3 : 1;
    for (var i = 0; i < waves; i++) {
      final phase = animate ? (t + i / waves) % 1.0 : 0.55;
      final r = maxR * (0.30 + phase * 0.70);
      final fade = animate ? (1.0 - phase) : 0.4;
      canvas.drawCircle(
        center,
        r,
        Paint()
          ..style = PaintingStyle.stroke
          ..strokeWidth = (active ? 2.5 : 1.8)
          ..color = T.accent.withValues(alpha: 0.35 * fade),
      );
    }

    // Outer soft amber glow
    final glowR = maxR * 0.38;
    canvas.drawCircle(
      center,
      glowR * 2.2,
      Paint()
        ..shader = RadialGradient(
          colors: [
            T.accent.withValues(alpha: active ? 0.18 : 0.10),
            T.accent.withValues(alpha: 0.0),
          ],
        ).createShader(Rect.fromCircle(center: center, radius: glowR * 2.2)),
    );

    // Central amber disc
    final discR = maxR * 0.30;
    canvas.drawCircle(
      center,
      discR,
      Paint()
        ..shader = LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [
            const Color(0xFFFBBF24), // warm amber
            T.accent,                // deeper amber
          ],
        ).createShader(Rect.fromCircle(center: center, radius: discR)),
    );

    // Specular highlight on the disc
    canvas.drawCircle(
      center,
      discR,
      Paint()
        ..shader = RadialGradient(
          center: const Alignment(-0.3, -0.4),
          radius: 0.9,
          colors: [
            Colors.white.withValues(alpha: 0.50),
            Colors.white.withValues(alpha: 0.0),
          ],
        ).createShader(Rect.fromCircle(center: center, radius: discR)),
    );

    // Thin bright rim
    canvas.drawCircle(
      center,
      discR,
      Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = 1.2
        ..color = Colors.white.withValues(alpha: 0.4),
    );

    // Bell glyph — white bell icon
    _drawBell(canvas, center, discR * 1.1, Colors.white);
  }

  void _drawBell(Canvas canvas, Offset c, double s, Color color) {
    final p = Paint()
      ..color = color
      ..style = PaintingStyle.fill;
    final path = Path();
    final w = s * 0.85;
    final h = s * 0.85;
    final top = c.dy - h * 0.55;
    // Bell body
    path.moveTo(c.dx - w * 0.5, c.dy + h * 0.30);
    path.quadraticBezierTo(
        c.dx - w * 0.5, top, c.dx, top - h * 0.10);
    path.quadraticBezierTo(
        c.dx + w * 0.5, top, c.dx + w * 0.5, c.dy + h * 0.30);
    path.lineTo(c.dx - w * 0.5, c.dy + h * 0.30);
    path.close();
    canvas.drawPath(path, p);
    // Clapper
    canvas.drawCircle(Offset(c.dx, c.dy + h * 0.42), s * 0.12, p);
    // Top knob
    canvas.drawCircle(Offset(c.dx, top - h * 0.16), s * 0.09, p);
  }

  @override
  bool shouldRepaint(_DoorbellPainter old) =>
      old.t != t || old.active != active || old.animate != animate;
}
