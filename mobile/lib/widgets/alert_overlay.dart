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

/// The signature full-screen doorbell alert.
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
            padding: const EdgeInsets.all(T.s20),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Row(
                  children: [
                    // Gradient bell tile — reads as "alert" at a glance.
                    Container(
                      width: 56,
                      height: 56,
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
                          color: Colors.white, size: 30),
                    ),
                    const SizedBox(width: T.s12),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text('Someone’s at the door',
                              style: text.headlineSmall?.copyWith(
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
                const SizedBox(height: T.s16),
                // Details sit in a dark panel so text stays legible while the
                // deaf-mode flash fills the screen around it.
                Expanded(
                  child: Container(
                    decoration: BoxDecoration(
                      color: T.bg2.withValues(alpha: 0.92),
                      borderRadius: BorderRadius.circular(T.rMd),
                      border: Border.all(
                          color: Colors.white.withValues(alpha: 0.12)),
                    ),
                    padding: const EdgeInsets.all(T.s16),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(e.countLine,
                            style: text.titleLarge
                                ?.copyWith(fontWeight: FontWeight.w700)),
                        const SizedBox(height: T.s16),
                        Expanded(
                          child: ListView(
                            children: [
                              if (e.people.isEmpty)
                                Text(
                                  e.sceneSummary.isNotEmpty
                                      ? e.sceneSummary
                                      : 'Motion detected at the door.',
                                  style: text.bodyLarge,
                                )
                              else
                                for (final p in e.people)
                                  Padding(
                                    padding:
                                        const EdgeInsets.only(bottom: T.s16),
                                    child: PersonTile(
                                        person: p, reidSeen: e.reidSeenCount),
                                  ),
                              if (e.hasSpeech) ...[
                                const SizedBox(height: T.s8),
                                Text('They said:',
                                    style: text.titleSmall?.copyWith(
                                        color: T.muted,
                                        fontWeight: FontWeight.w700)),
                                const SizedBox(height: T.s4),
                                Text(
                                  (e.translatedTranscript.trim().isNotEmpty
                                          ? e.translatedTranscript
                                          : e.speechTranscript)
                                      .trim(),
                                  style: text.bodyLarge
                                      ?.copyWith(fontStyle: FontStyle.italic),
                                ),
                              ],
                            ],
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
                const SizedBox(height: T.s16),
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
}
