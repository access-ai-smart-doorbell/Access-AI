import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../core/format.dart';
import '../core/glass.dart';
import '../core/tokens.dart';
import '../models/visitor_event.dart';
import '../state/providers.dart';
import 'person_tile.dart';

/// A rich card summarising one visitor event. Shows a camera snapshot at the
/// top with "Door Camera" overlay, per-person identity tiles with confidence,
/// a scene description block, speech/audio block, carried objects, and hazards.
///
/// Redesigned for the bright accessible UI: white cards with soft shadows,
/// generous rounded corners, clear hierarchy with dark navy text.
class EventCard extends ConsumerWidget {
  const EventCard({
    super.key,
    required this.event,
    this.onTap,
    this.heroTag,
    this.expanded = false,
    this.showSnapshot = true,
  });

  final VisitorEvent event;
  final VoidCallback? onTap;
  final String? heroTag;
  final bool expanded;

  /// Whether to show the camera snapshot image at the top. True by default;
  /// set to false when the snapshot is already shown separately (e.g. detail
  /// screen shows it above the card).
  final bool showSnapshot;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final text = Theme.of(context).textTheme;
    final api = ref.watch(apiProvider);

    final people = event.people;
    final shown = expanded ? people : people.take(2).toList();
    final hiddenCount = people.length - shown.length;

    Widget card = GlassCard(
      onTap: onTap,
      padding: EdgeInsets.zero,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // ── Camera snapshot ──────────────────────────────────────────
          if (showSnapshot && event.eventId.isNotEmpty)
            ClipRRect(
              borderRadius: const BorderRadius.vertical(
                  top: Radius.circular(T.rLg)),
              child: Stack(
                children: [
                  AspectRatio(
                    aspectRatio: 16 / 9,
                    child: Image.network(
                      api.snapshotUrl(event.eventId),
                      fit: BoxFit.cover,
                      gaplessPlayback: true,
                      errorBuilder: (_, _, _) => Container(
                        color: T.bgSubtle,
                        child: Center(
                          child: Column(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              Icon(Icons.videocam_outlined,
                                  size: 36,
                                  color: T.textTertiary),
                              const SizedBox(height: T.s4),
                              Text('No snapshot available',
                                  style: text.labelSmall?.copyWith(
                                      color: T.textTertiary)),
                            ],
                          ),
                        ),
                      ),
                      loadingBuilder: (context, child, progress) =>
                          progress == null
                              ? child
                              : Container(
                                  color: T.bgSubtle,
                                  child: const Center(
                                      child: CircularProgressIndicator()),
                                ),
                    ),
                  ),
                  // "Door Camera" overlay badge
                  Positioned(
                    left: T.s12,
                    top: T.s12,
                    child: Container(
                      padding: const EdgeInsets.symmetric(
                          horizontal: T.s8, vertical: T.s4),
                      decoration: BoxDecoration(
                        color: Colors.black.withValues(alpha: 0.55),
                        borderRadius: BorderRadius.circular(T.s6),
                      ),
                      child: Row(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          const Icon(Icons.videocam,
                              size: 13, color: Colors.white),
                          const SizedBox(width: T.s4),
                          Text('Door Camera',
                              style: text.labelSmall?.copyWith(
                                  color: Colors.white,
                                  fontWeight: FontWeight.w600,
                                  fontSize: 11)),
                        ],
                      ),
                    ),
                  ),
                  // Timestamp badge
                  Positioned(
                    right: T.s12,
                    top: T.s12,
                    child: Container(
                      padding: const EdgeInsets.symmetric(
                          horizontal: T.s8, vertical: T.s4),
                      decoration: BoxDecoration(
                        color: T.primary,
                        borderRadius: BorderRadius.circular(T.s6),
                      ),
                      child: Text(
                        prettyTime(event.timestamp),
                        style: text.labelSmall?.copyWith(
                            color: Colors.white,
                            fontWeight: FontWeight.w600,
                            fontSize: 11),
                      ),
                    ),
                  ),
                ],
              ),
            ),

          // ── Card body ───────────────────────────────────────────────
          Padding(
            padding: const EdgeInsets.all(T.s16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                // Header: count + time
                Row(
                  crossAxisAlignment: CrossAxisAlignment.center,
                  children: [
                    // People count icon
                    Container(
                      width: 40,
                      height: 40,
                      decoration: BoxDecoration(
                        shape: BoxShape.circle,
                        color: _statusBg,
                      ),
                      child: Icon(
                        _statusIcon,
                        size: 20,
                        color: _statusColor,
                      ),
                    ),
                    const SizedBox(width: T.s12),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            event.countLine,
                            style: text.titleMedium?.copyWith(
                              fontWeight: FontWeight.w700,
                              color: T.textPrimary,
                              fontSize: 15,
                            ),
                          ),
                          if (event.timestamp.isNotEmpty &&
                              !showSnapshot)
                            Text(
                              prettyTime(event.timestamp),
                              style: text.bodySmall?.copyWith(
                                color: T.textTertiary,
                                fontSize: 12,
                              ),
                            ),
                        ],
                      ),
                    ),
                  ],
                ),

                if (event.anySpoof)
                  Padding(
                    padding: const EdgeInsets.only(top: T.s8),
                    child: _Banner(
                      icon: Icons.warning_amber,
                      color: T.danger,
                      bgColor: T.dangerBg,
                      text: 'A face here may be a photo, not a live person.',
                    ),
                  ),

                const SizedBox(height: T.s12),

                // ── Per-person identity tiles ─────────────────────────
                if (people.isEmpty)
                  Text(
                    event.sceneSummary.isNotEmpty
                        ? event.sceneSummary
                        : 'Motion detected at the door.',
                    style: text.bodyMedium?.copyWith(
                      color: T.textSecondary,
                      height: 1.5,
                    ),
                  )
                else
                  for (int i = 0; i < shown.length; i++) ...[
                    if (i > 0)
                      Divider(color: T.border, height: T.s16),
                    PersonTile(
                        person: shown[i], reidSeen: event.reidSeenCount),
                  ],

                if (hiddenCount > 0)
                  Padding(
                    padding: const EdgeInsets.only(top: T.s8),
                    child: Text('+ $hiddenCount more',
                        style: text.labelLarge?.copyWith(
                            color: T.primary,
                            fontWeight: FontWeight.w600)),
                  ),

                // ── Scene description ─────────────────────────────────
                if (event.sceneSummary.isNotEmpty &&
                    people.isNotEmpty) ...[
                  const SizedBox(height: T.s16),
                  _SceneBlock(sceneSummary: event.sceneSummary),
                ],

                // ── Speech / audio ────────────────────────────────────
                if (event.hasSpeech) ...[
                  const SizedBox(height: T.s12),
                  _SpeechBlock(event: event),
                ],

                // ── Carried objects ───────────────────────────────────
                if (event.carriedObjects.isNotEmpty) ...[
                  const SizedBox(height: T.s12),
                  Wrap(
                    spacing: T.s8,
                    runSpacing: T.s8,
                    children: [
                      for (final o in event.carriedObjects)
                        _Tag(o, Icons.shopping_bag_outlined),
                    ],
                  ),
                ],

                // ── Hazards ───────────────────────────────────────────
                if (event.hazards.isNotEmpty &&
                    event.hazards != 'none') ...[
                  const SizedBox(height: T.s12),
                  _Banner(
                    icon: Icons.report_problem,
                    color: T.unknown,
                    bgColor: T.unknownBg,
                    text: 'Note: ${event.hazards}',
                  ),
                ],

                // ── View details button (compact mode only) ───────────
                if (!expanded && onTap != null) ...[
                  const SizedBox(height: T.s16),
                  Center(
                    child: TextButton.icon(
                      onPressed: onTap,
                      icon: Text('View full details',
                          style: text.labelLarge?.copyWith(
                            color: T.primary,
                            fontWeight: FontWeight.w600,
                          )),
                      label: Icon(Icons.arrow_forward,
                          size: 16, color: T.primary),
                    ),
                  ),
                ],
              ],
            ),
          ),
        ],
      ),
    );

    if (heroTag != null) {
      card = Hero(
          tag: heroTag!,
          child: Material(type: MaterialType.transparency, child: card));
    }
    return card;
  }

  Color get _statusColor {
    if (event.anySpoof) return T.danger;
    if (event.totalPeople > 1) return T.multi;
    if (event.knownCount > 0) return T.known;
    if (event.totalPeople > 0) return T.unknown;
    return T.textTertiary;
  }

  Color get _statusBg {
    if (event.anySpoof) return T.dangerBg;
    if (event.totalPeople > 1) return T.multiBg;
    if (event.knownCount > 0) return T.knownBg;
    if (event.totalPeople > 0) return T.unknownBg;
    return T.bgSubtle;
  }

  IconData get _statusIcon {
    if (event.anySpoof) return Icons.warning_amber;
    if (event.totalPeople > 1) return Icons.groups;
    if (event.knownCount > 0) return Icons.person;
    if (event.totalPeople > 0) return Icons.person_outline;
    return Icons.sensors_off;
  }
}

/// Scene description block — visually distinct section showing the full
/// AI-generated scene narrative. This is the most important section for
/// accessibility.
class _SceneBlock extends StatelessWidget {
  const _SceneBlock({required this.sceneSummary});
  final String sceneSummary;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;

    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(T.s12),
      decoration: BoxDecoration(
        color: T.bgSubtle,
        borderRadius: BorderRadius.circular(T.rSm),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(Icons.visibility, size: 16, color: T.textTertiary),
              const SizedBox(width: T.s8),
              Text('Scene description',
                  style: text.labelMedium?.copyWith(
                      color: T.textTertiary,
                      fontWeight: FontWeight.w600)),
            ],
          ),
          const SizedBox(height: T.s8),
          Text(sceneSummary,
              style: text.bodyMedium?.copyWith(
                  color: T.textSecondary,
                  height: 1.5)),
        ],
      ),
    );
  }
}

class _SpeechBlock extends StatelessWidget {
  const _SpeechBlock({required this.event});
  final VisitorEvent event;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final original = event.speechTranscript.trim();
    final translated = event.translatedTranscript.trim();
    final showTranslated = translated.isNotEmpty && translated != original;

    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(T.s12),
      decoration: BoxDecoration(
        color: T.primaryLighter,
        borderRadius: BorderRadius.circular(T.rSm),
        border: Border.all(color: T.primary.withValues(alpha: 0.15)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(Icons.record_voice_over, size: 16, color: T.primary),
              const SizedBox(width: T.s8),
              Text('Visitor said',
                  style: text.labelMedium?.copyWith(
                      color: T.primary, fontWeight: FontWeight.w600)),
              if (event.languageDetected.isNotEmpty &&
                  event.languageDetected != 'en') ...[
                const SizedBox(width: T.s8),
                Text('(${event.languageDetected})',
                    style: text.labelSmall?.copyWith(
                        color: T.textTertiary)),
              ],
            ],
          ),
          const SizedBox(height: T.s6),
          Text('"${showTranslated ? translated : original}"',
              style: text.bodyMedium?.copyWith(
                  fontStyle: FontStyle.italic,
                  color: T.textPrimary,
                  height: 1.5)),
          if (showTranslated && original.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(top: 2),
              child: Text('Original: $original',
                  style: text.bodySmall?.copyWith(
                      color: T.textTertiary)),
            ),
        ],
      ),
    );
  }
}

class _Banner extends StatelessWidget {
  const _Banner({
    required this.icon,
    required this.color,
    required this.bgColor,
    required this.text,
  });
  final IconData icon;
  final Color color;
  final Color bgColor;
  final String text;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(horizontal: T.s12, vertical: T.s8),
      decoration: BoxDecoration(
        color: bgColor,
        borderRadius: BorderRadius.circular(T.rSm),
      ),
      child: Row(
        children: [
          Icon(icon, size: 18, color: color),
          const SizedBox(width: T.s8),
          Expanded(
              child: Text(text,
                  style: Theme.of(context)
                      .textTheme
                      .bodySmall
                      ?.copyWith(
                        fontWeight: FontWeight.w600,
                        color: color,
                      ))),
        ],
      ),
    );
  }
}

class _Tag extends StatelessWidget {
  const _Tag(this.label, this.icon);
  final String label;
  final IconData icon;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: T.s12, vertical: T.s6),
      decoration: BoxDecoration(
        color: T.bgSubtle,
        borderRadius: BorderRadius.circular(999),
        border: Border.all(color: T.border),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 14, color: T.textSecondary),
          const SizedBox(width: T.s4),
          Text(label,
              style: Theme.of(context).textTheme.labelMedium?.copyWith(
                    color: T.textPrimary,
                    fontWeight: FontWeight.w500,
                  )),
        ],
      ),
    );
  }
}
