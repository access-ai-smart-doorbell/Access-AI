import 'package:flutter/material.dart';

import '../core/format.dart';
import '../core/tokens.dart';
import '../models/visitor_event.dart';

/// Renders ONE detected person under the project's strict presentation rules:
///
/// - KNOWN person → their name + a "Known" chip, plus appearance / mood cues.
///   NEVER their age or gender.
/// - UNKNOWN person → a cautious description: age as an approximate *band*
///   (never a raw number), a hedged expression ("appears calm"), and appearance.
/// - A likely spoof gets a prominent red "⚠ Possible photo" flag and is treated
///   as unverified (not counted as a trusted known match).
class PersonTile extends StatelessWidget {
  const PersonTile({super.key, required this.person, this.reidSeen = 0});

  final Person person;

  /// If > 1, this identity has been seen before (repeat visitor).
  final int reidSeen;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final text = Theme.of(context).textTheme;
    final trusted = person.known && !person.isSpoof;

    final title = trusted ? person.name : 'Unknown visitor';
    final descLines = _describe(person);
    final semantic = _semanticSummary(title, person, descLines);

    return Semantics(
      label: semantic,
      container: true,
      child: ExcludeSemantics(
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            _Avatar(known: trusted, spoof: person.isSpoof),
            const SizedBox(width: T.s12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Flexible(
                        child: Text(
                          title,
                          style: text.titleMedium
                              ?.copyWith(fontWeight: FontWeight.w700),
                          overflow: TextOverflow.ellipsis,
                        ),
                      ),
                      const SizedBox(width: T.s8),
                      if (trusted) _Chip('Known', T.success, Icons.verified),
                      if (person.isSpoof)
                        _Chip('⚠ Possible photo', T.danger, Icons.warning_amber),
                      if (trusted && reidSeen > 1)
                        Padding(
                          padding: const EdgeInsets.only(left: T.s8),
                          child: _Chip('Seen before', cs.primary, Icons.history),
                        ),
                    ],
                  ),
                  for (final line in descLines)
                    Padding(
                      padding: const EdgeInsets.only(top: T.s4),
                      child: Text(line,
                          style: text.bodyMedium?.copyWith(
                              color: cs.onSurface.withValues(alpha: 0.82))),
                    ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  /// Build the visible description lines per the rendering rule.
  List<String> _describe(Person p) {
    final lines = <String>[];
    final trusted = p.known && !p.isSpoof;

    if (!trusted) {
      // Unknown: approximate age band + gender (both cautious).
      final band = ageBand(p.age);
      final g = p.gender.trim();
      if (band.isNotEmpty && g.isNotEmpty) {
        lines.add('$band, $g');
      } else if (band.isNotEmpty) {
        lines.add(_cap(band));
      } else if (g.isNotEmpty) {
        lines.add('Appears to be a $g');
      }
    }

    if (p.appearance.trim().isNotEmpty) lines.add(_cap(p.appearance.trim()));
    if (p.clothing.trim().isNotEmpty) lines.add(_cap(p.clothing.trim()));
    if (p.action.trim().isNotEmpty) lines.add(_cap(p.action.trim()));
    if (p.position.trim().isNotEmpty) lines.add(_cap(p.position.trim()));

    final expr = hedgedExpression(p.expression);
    if (expr.isNotEmpty) lines.add(_cap(expr));

    if (p.isSpoof) {
      lines.add('This face may be a photo or screen, not a real person.');
    }
    return lines;
  }

  String _semanticSummary(String title, Person p, List<String> lines) {
    final b = StringBuffer(title);
    if (p.isSpoof) b.write(', possible photo');
    for (final l in lines) {
      b.write('. ');
      b.write(l);
    }
    return b.toString();
  }

  static String _cap(String s) =>
      s.isEmpty ? s : s[0].toUpperCase() + s.substring(1);
}

class _Avatar extends StatelessWidget {
  const _Avatar({required this.known, required this.spoof});
  final bool known;
  final bool spoof;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final color = spoof
        ? T.danger
        : known
            ? T.success
            : cs.primary;
    final icon = spoof
        ? Icons.report_gmailerrorred
        : known
            ? Icons.person
            : Icons.person_outline;
    // Gradient ring wraps the disc: aurora for trusted known people, a solid
    // state colour otherwise (danger for spoof, primary for unknown).
    return Container(
      width: 52,
      height: 52,
      padding: const EdgeInsets.all(2.5),
      decoration: BoxDecoration(
        shape: BoxShape.circle,
        gradient: known && !spoof
            ? T.aurora
            : LinearGradient(colors: [
                color.withValues(alpha: 0.85),
                color.withValues(alpha: 0.45),
              ]),
        boxShadow: [
          BoxShadow(
            color: color.withValues(alpha: 0.25),
            blurRadius: 14,
            spreadRadius: -2,
          ),
        ],
      ),
      child: Container(
        decoration: BoxDecoration(
          color: Color.alphaBlend(color.withValues(alpha: 0.16), T.bg2),
          shape: BoxShape.circle,
        ),
        child: Icon(icon, color: color, size: 24),
      ),
    );
  }
}

class _Chip extends StatelessWidget {
  const _Chip(this.label, this.color, this.icon);
  final String label;
  final Color color;
  final IconData icon;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: T.s8, vertical: 3),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.16),
        borderRadius: BorderRadius.circular(999),
        border: Border.all(color: color.withValues(alpha: 0.55)),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 13, color: color),
          const SizedBox(width: 4),
          Text(label,
              style: Theme.of(context)
                  .textTheme
                  .labelSmall
                  ?.copyWith(color: color, fontWeight: FontWeight.w700)),
        ],
      ),
    );
  }
}
