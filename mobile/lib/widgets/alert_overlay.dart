import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../core/format.dart';
import '../core/motion.dart';
import '../core/tokens.dart';
import '../models/visitor_event.dart';
import '../services/api_service.dart';
import '../services/audio_service.dart';
import 'person_tile.dart';

/// The signature full-screen doorbell alert — redesigned as a rich "AI door
/// event" rather than a minimal notification.
///
/// Layout (top to bottom):
///   1. Header: bell icon + "Someone's at the door" + timestamp
///   2. Camera snapshot (from `/snapshot/{event_id}`)
///   3. Per-person identity tiles with confidence
///   4. Scene description (the AI's narrative of what it sees)
///   5. Speech / visitor message (if any)
///   6. Action buttons: Repeat (blind mode) + Dismiss
///
/// Deaf / Both → a bold caption plus a gentle attention flash (kept BELOW 3
/// flashes per second — WCAG 2.3.1 — and disabled entirely under reduce-motion,
/// where a steady high-contrast panel is shown instead) and a distinct vibration.
/// Blind / Both → speaks the announcement (server Kokoro voice, falling back to
/// on-device TTS) and pushes it to the screen reader via SemanticsService.
///
/// It never gates function: any user can read the details and dismiss.
class DoorbellAlert extends StatefulWidget {
  const DoorbellAlert({
    super.key,
    required this.event,
    required this.mode,
    required this.audio,
    required this.api,
  });

  final VisitorEvent event;
  final String mode; // blind | deaf | both
  final AudioService audio;
  final ApiService api;

  /// Push as a full-screen route.
  static Future<void> show(
    BuildContext context, {
    required VisitorEvent event,
    required String mode,
    required AudioService audio,
    required ApiService api,
  }) {
    return Navigator.of(context, rootNavigator: true).push(
      PageRouteBuilder(
        opaque: false,
        barrierColor: Colors.black54,
        transitionDuration: const Duration(milliseconds: 220),
        pageBuilder: (_, _, _) =>
            DoorbellAlert(event: event, mode: mode, audio: audio, api: api),
      ),
    );
  }

  @override
  State<DoorbellAlert> createState() => _DoorbellAlertState();
}

class _DoorbellAlertState extends State<DoorbellAlert>
    with SingleTickerProviderStateMixin {
  late final AnimationController _flash;
  Timer? _escalate; // Phase 17: re-buzz until acknowledged
  bool get _visual => widget.mode == 'deaf' || widget.mode == 'both';
  bool get _spoken => widget.mode == 'blind' || widget.mode == 'both';

  @override
  void initState() {
    super.initState();
    _flash = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 700), // ~1.4 Hz — seizure-safe
    );
    // Fire outputs after the first frame so context/media queries are ready.
    WidgetsBinding.instance.addPostFrameCallback((_) => _fire());
  }

  void _fire() {
    if (!mounted) return;
    final reduce = context.reduceMotion;
    if (_visual) {
      // Phase 17: the vibration rhythm identifies the visitor type by feel
      // (known / delivery / unknown / spoof), and the alert ESCALATES —
      // re-buzzing every 6s while unacknowledged — so one missed pulse is
      // not a missed visitor. Dismissing the overlay stops it.
      widget.audio.kindVibrate(widget.event.kind);
      if (!reduce) _flash.repeat(reverse: true);
      _escalate?.cancel();
      var rounds = 0;
      _escalate = Timer.periodic(const Duration(seconds: 6), (t) {
        if (!mounted || ++rounds >= 5) {
          t.cancel();
          return;
        }
        widget.audio.kindVibrate(widget.event.kind);
      });
    }
    if (_spoken) {
      final text = _spokenText(widget.event);
      widget.audio.speak(text, widget.api);
    } else {
      // Even in deaf mode, announce for any screen reader the user runs.
      widget.audio.announceOnly(_spokenText(widget.event));
    }
  }

  String _spokenText(VisitorEvent e) {
    if (e.announcementText.trim().isNotEmpty) return e.announcementText.trim();
    final b = StringBuffer('Someone is at the door. ');
    b.write(e.countLine);
    if (e.people.isNotEmpty) {
      final p = e.people.first;
      if (p.known && !p.isSpoof) {
        b.write('. ${p.name} is here');
        if (p.confidence > 0) {
          b.write(', ${(p.confidence * 100).round()} percent match');
        }
      }
    }
    if (e.sceneSummary.isNotEmpty) {
      b.write('. ${e.sceneSummary}');
    }
    if (e.hasSpeech) {
      final speech = e.translatedTranscript.trim().isNotEmpty
          ? e.translatedTranscript.trim()
          : e.speechTranscript.trim();
      if (speech.isNotEmpty) {
        b.write('. They said: $speech');
      }
    }
    if (e.anySpoof) b.write('. Caution: a face may be a photo');
    return b.toString();
  }

  @override
  void dispose() {
    _escalate?.cancel();
    _flash.dispose();
    widget.audio.stop();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final text = Theme.of(context).textTheme;
    final reduce = context.reduceMotion;
    final e = widget.event;

    return Semantics(
      liveRegion: true,
      label: 'Doorbell. ${_spokenText(e)}',
      child: AnimatedBuilder(
        animation: _flash,
        builder: (context, child) {
          // Attention background: flashing (safe) for deaf, else a calm scrim.
          final Color bg;
          if (_visual && !reduce) {
            bg = Color.lerp(cs.surface, T.deafFlash.withValues(alpha: 0.9),
                _flash.value)!;
          } else if (_visual) {
            bg = Color.lerp(cs.surface, T.deafFlash, 0.35)!;
          } else {
            bg = cs.surface;
          }
          return ColoredBox(color: bg, child: child);
        },
        child: SafeArea(
          child: Padding(
            padding: const EdgeInsets.all(T.s16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                // ── Header ────────────────────────────────────────────
                Row(
                  children: [
                    // Gradient bell tile — reads as "alert" at a glance.
                    Container(
                      width: 52,
                      height: 52,
                      decoration: BoxDecoration(
                        gradient: T.aurora,
                        borderRadius: BorderRadius.circular(T.rSm),
                        boxShadow: [
                          BoxShadow(
                            color: T.jarvis2.withValues(alpha: 0.4),
                            blurRadius: 20,
                            spreadRadius: -2,
                          ),
                        ],
                      ),
                      child: const Icon(Icons.notifications_active,
                          color: Colors.white, size: 26),
                    ),
                    const SizedBox(width: T.s12),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text('Someone\u2019s at the door',
                              style: text.titleLarge?.copyWith(
                                  fontWeight: FontWeight.w800,
                                  letterSpacing: -0.5)),
                          const SizedBox(height: 2),
                          Text(prettyTime(e.timestamp),
                              style: text.labelLarge?.copyWith(
                                  color: cs.onSurface
                                      .withValues(alpha: 0.6))),
                        ],
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: T.s12),

                // ── Scrollable event detail ───────────────────────────
                Expanded(
                  child: Container(
                    decoration: BoxDecoration(
                      color: T.bg2.withValues(alpha: 0.92),
                      borderRadius: BorderRadius.circular(T.rMd),
                      border: Border.all(
                          color: Colors.white.withValues(alpha: 0.12)),
                    ),
                    child: ClipRRect(
                      borderRadius: BorderRadius.circular(T.rMd),
                      child: ListView(
                        padding: EdgeInsets.zero,
                        children: [
                          // ── Camera snapshot ─────────────────────────
                          if (e.eventId.isNotEmpty)
                            AspectRatio(
                              aspectRatio: 16 / 9,
                              child: Image.network(
                                widget.api.snapshotUrl(e.eventId),
                                fit: BoxFit.cover,
                                gaplessPlayback: true,
                                errorBuilder: (_, _, _) => Container(
                                  color: cs.surfaceContainerHighest,
                                  child: Center(
                                    child: Column(
                                      mainAxisSize: MainAxisSize.min,
                                      children: [
                                        Icon(Icons.videocam_outlined,
                                            size: 40,
                                            color: cs.onSurface
                                                .withValues(alpha: 0.4)),
                                        const SizedBox(height: T.s8),
                                        Text('Camera snapshot unavailable',
                                            style: text.labelMedium?.copyWith(
                                                color: cs.onSurface
                                                    .withValues(alpha: 0.4))),
                                      ],
                                    ),
                                  ),
                                ),
                                loadingBuilder:
                                    (context, child, progress) =>
                                        progress == null
                                            ? child
                                            : Container(
                                                color: cs
                                                    .surfaceContainerHighest,
                                                child: const Center(
                                                    child:
                                                        CircularProgressIndicator()),
                                              ),
                              ),
                            ),

                          // ── Identity + details ─────────────────────
                          Padding(
                            padding: const EdgeInsets.all(T.s16),
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                // Count line
                                Text(e.countLine,
                                    style: text.titleMedium?.copyWith(
                                        fontWeight: FontWeight.w700)),
                                const SizedBox(height: T.s16),

                                // Spoof warning
                                if (e.anySpoof) ...[
                                  _AlertBanner(
                                    icon: Icons.warning_amber,
                                    color: T.danger,
                                    text:
                                        'A face here may be a photo, not a live person.',
                                  ),
                                  const SizedBox(height: T.s12),
                                ],

                                // Per-person tiles
                                if (e.people.isEmpty && e.sceneSummary.isEmpty)
                                  Text('Motion detected at the door.',
                                      style: text.bodyLarge)
                                else
                                  for (final p in e.people) ...[
                                    PersonTile(
                                        person: p,
                                        reidSeen: e.reidSeenCount),
                                    const SizedBox(height: T.s12),
                                  ],

                                // ── Scene description ────────────────
                                if (e.sceneSummary.isNotEmpty) ...[
                                  const SizedBox(height: T.s4),
                                  _SectionBlock(
                                    icon: Icons.visibility,
                                    label: 'Scene description',
                                    child: Text(
                                      '"${e.sceneSummary}"',
                                      style: text.bodyMedium?.copyWith(
                                          fontStyle: FontStyle.italic,
                                          height: 1.5),
                                    ),
                                  ),
                                ],

                                // ── Speech / audio ───────────────────
                                if (e.hasSpeech) ...[
                                  const SizedBox(height: T.s12),
                                  _speechSection(context, e),
                                ],

                                // ── Carried objects ──────────────────
                                if (e.carriedObjects.isNotEmpty) ...[
                                  const SizedBox(height: T.s12),
                                  Wrap(
                                    spacing: T.s8,
                                    runSpacing: T.s8,
                                    children: [
                                      for (final o in e.carriedObjects)
                                        _ObjectChip(o),
                                    ],
                                  ),
                                ],

                                // ── Hazards ──────────────────────────
                                if (e.hazards.isNotEmpty &&
                                    e.hazards != 'none') ...[
                                  const SizedBox(height: T.s12),
                                  _AlertBanner(
                                      icon: Icons.report_problem,
                                      color: T.accent,
                                      text: 'Note: ${e.hazards}'),
                                ],
                              ],
                            ),
                          ),
                        ],
                      ),
                    ),
                  ),
                ),

                const SizedBox(height: T.s12),

                // ── Action buttons ────────────────────────────────────
                Row(
                  children: [
                    if (_spoken)
                      Expanded(
                        child: SizedBox(
                          height: T.minTouch,
                          child: OutlinedButton.icon(
                            onPressed: () =>
                                widget.audio.speak(_spokenText(e), widget.api),
                            icon: const Icon(Icons.volume_up),
                            label: const Text('Repeat'),
                          ),
                        ),
                      ),
                    if (_spoken) const SizedBox(width: T.s12),
                    Expanded(
                      child: SizedBox(
                        height: T.minTouch,
                        child: FilledButton.icon(
                          onPressed: () {
                            HapticFeedback.selectionClick();
                            Navigator.of(context).maybePop();
                          },
                          icon: const Icon(Icons.check),
                          label: const Text('Dismiss'),
                        ),
                      ),
                    ),
                  ],
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _speechSection(BuildContext context, VisitorEvent e) {
    final cs = Theme.of(context).colorScheme;
    final text = Theme.of(context).textTheme;
    final original = e.speechTranscript.trim();
    final translated = e.translatedTranscript.trim();
    final showTranslated = translated.isNotEmpty && translated != original;

    return _SectionBlock(
      icon: Icons.record_voice_over,
      label: 'Visitor said',
      iconColor: cs.primary,
      labelColor: cs.primary,
      borderColor: cs.primary.withValues(alpha: 0.3),
      bgColor: cs.primary.withValues(alpha: 0.08),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('"${showTranslated ? translated : original}"',
              style: text.bodyMedium?.copyWith(
                  fontStyle: FontStyle.italic, height: 1.5)),
          if (showTranslated && original.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(top: 2),
              child: Text('Original: $original',
                  style: text.bodySmall?.copyWith(
                      color: cs.onSurface.withValues(alpha: 0.55))),
            ),
          if (e.languageDetected.isNotEmpty && e.languageDetected != 'en')
            Padding(
              padding: const EdgeInsets.only(top: 2),
              child: Text('Language: ${e.languageDetected}',
                  style: text.labelSmall?.copyWith(
                      color: cs.onSurface.withValues(alpha: 0.5))),
            ),
        ],
      ),
    );
  }
}

/// A visually distinct info section — icon + label header, coloured background,
/// rounded corners. Used for scene description and speech.
class _SectionBlock extends StatelessWidget {
  const _SectionBlock({
    required this.icon,
    required this.label,
    required this.child,
    this.iconColor,
    this.labelColor,
    this.borderColor,
    this.bgColor,
  });

  final IconData icon;
  final String label;
  final Widget child;
  final Color? iconColor;
  final Color? labelColor;
  final Color? borderColor;
  final Color? bgColor;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final text = Theme.of(context).textTheme;
    final ic = iconColor ?? cs.onSurface.withValues(alpha: 0.7);
    final lc = labelColor ?? cs.onSurface.withValues(alpha: 0.7);

    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(T.s12),
      decoration: BoxDecoration(
        color: bgColor ?? cs.surfaceContainerHighest.withValues(alpha: 0.5),
        borderRadius: BorderRadius.circular(T.rSm),
        border: Border.all(
            color: borderColor ?? cs.onSurface.withValues(alpha: 0.08)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(icon, size: 16, color: ic),
              const SizedBox(width: T.s8),
              Text(label,
                  style: text.labelMedium
                      ?.copyWith(color: lc, fontWeight: FontWeight.w700)),
            ],
          ),
          const SizedBox(height: T.s8),
          child,
        ],
      ),
    );
  }
}

class _AlertBanner extends StatelessWidget {
  const _AlertBanner(
      {required this.icon, required this.color, required this.text});
  final IconData icon;
  final Color color;
  final String text;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(horizontal: T.s12, vertical: T.s8),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.14),
        borderRadius: BorderRadius.circular(T.rSm),
        border: Border.all(color: color.withValues(alpha: 0.5)),
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
                      ?.copyWith(fontWeight: FontWeight.w600))),
        ],
      ),
    );
  }
}

class _ObjectChip extends StatelessWidget {
  const _ObjectChip(this.label);
  final String label;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: T.s12, vertical: T.s8),
      decoration: BoxDecoration(
        color: cs.primary.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(999),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(Icons.shopping_bag_outlined, size: 14, color: cs.primary),
          const SizedBox(width: T.s4),
          Text(label, style: Theme.of(context).textTheme.labelMedium),
        ],
      ),
    );
  }
}
