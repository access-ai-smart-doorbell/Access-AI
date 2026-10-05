import 'package:flutter/material.dart';

import '../core/format.dart';
import '../core/tokens.dart';
import '../models/visitor_event.dart';

/// Renders ONE detected person under the project's strict presentation rules:
///
/// - KNOWN person → their name + a "Known" chip with confidence %, plus
///   appearance / mood cues. NEVER their age or gender.
/// - UNKNOWN person → a cautious description: age as an approximate *band*
///   (never a raw number), a hedged expression ("appears calm"), and appearance.
/// - A likely spoof gets a prominent red "⚠ Possible photo" flag and is treated
///   as unverified (not counted as a trusted known match).
///
/// Redesigned for the bright accessible UI: circular avatars with soft colored
/// backgrounds, clean chips with semantic colors, generous spacing.
class PersonTile extends StatelessWidget {
  const PersonTile({super.key, required this.person, this.reidSeen = 0});

  final Person person;

  /// If > 1, this identity has been seen before (repeat visitor).
  final int reidSeen;

  @override
  Widget build(BuildContext context) {
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
                  // Name
                  Text(
                    title,
                    style: text.titleMedium?.copyWith(
                      fontWeight: FontWeight.w700,
                      color: T.textPrimary,
                      fontSize: 17,
                    ),
                    overflow: TextOverflow.ellipsis,
                  ),
                  const SizedBox(height: T.s6),
                  // Status chips row
                  Wrap(
                    spacing: T.s8,
                    runSpacing: T.s4,
                    children: [
                      if (trusted)
                        _Chip('Known', T.known, T.knownBg, Icons.check_circle),
                      if (trusted && person.confidence > 0)
                        _Chip(
                          '${(person.confidence * 100).round()}% match',
                          T.primary,
                          T.primaryLighter,
                          Icons.bar_chart,
                        ),
                      if (!trusted && !person.isSpoof)
                        _Chip('Unknown', T.unknown, T.unknownBg,
                            Icons.warning_amber_rounded),
                      if (person.isSpoof)
                        _Chip('⚠ Possible photo', T.danger, T.dangerBg,
                            Icons.warning_amber),
                      if (trusted && reidSeen > 1)
                        _Chip('Seen before', T.primary, T.primaryLighter,
                            Icons.history),
                    ],
                  ),
                  // Description lines
                  for (final line in descLines)
                    Padding(
                      padding: const EdgeInsets.only(top: T.s4),
                      child: Text(line,
                          style: text.bodyMedium?.copyWith(
                              color: T.textSecondary,
                              height: 1.4)),
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
    if (p.known && !p.isSpoof && p.confidence > 0) {
      b.write(', ${(p.confidence * 100).round()} percent match');
    }
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
    final color = spoof
        ? T.danger
        : known
            ? T.known
            : T.unknown;
    final bgColor = spoof
        ? T.dangerBg
        : known
            ? T.knownBg
            : T.unknownBg;
    final icon = spoof
        ? Icons.report_gmailerrorred
        : known
            ? Icons.person
            : Icons.person_outline;

    return Container(
      width: 52,
      height: 52,
      decoration: BoxDecoration(
        shape: BoxShape.circle,
        color: bgColor,
        border: Border.all(
          color: color.withValues(alpha: 0.3),
          width: 2,
        ),
      ),
      child: Icon(icon, color: color, size: 26),
    );
  }
}

class _Chip extends StatelessWidget {
  const _Chip(this.label, this.color, this.bgColor, this.icon);
  final String label;
  final Color color;
  final Color bgColor;
  final IconData icon;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: T.s8, vertical: 4),
      decoration: BoxDecoration(
        color: bgColor,
        borderRadius: BorderRadius.circular(999),
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
                  ?.copyWith(
                    color: color,
                    fontWeight: FontWeight.w600,
                    fontSize: 12,
                  )),
        ],
      ),
    );
  }
}
