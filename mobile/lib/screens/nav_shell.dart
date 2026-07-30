import 'dart:ui';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../core/parse.dart';
import '../core/tokens.dart';
import '../core/ui.dart';
import '../models/visitor_event.dart';
import '../services/audio_service.dart';
import '../state/providers.dart';
import '../widgets/alert_overlay.dart';
import 'history_screen.dart';
import 'home_screen.dart';
import 'live_screen.dart';
import 'people_screen.dart';
import 'settings_screen.dart';

/// The app's home scaffold: a floating liquid-glass bottom navigation bar over
/// the five primary screens, kept alive with an IndexedStack so state (scroll,
/// live stream) survives tab switches. It also hosts the ALWAYS-ON live-alert
/// listener — while the user is anywhere in the shell (or a screen pushed
/// above it), an incoming doorbell event plays the doorbell earcon and
/// triggers the full-screen [DoorbellAlert].
class NavShell extends ConsumerStatefulWidget {
  const NavShell({super.key});

  @override
  ConsumerState<NavShell> createState() => _NavShellState();
}

class _NavShellState extends ConsumerState<NavShell> {
  int _index = 0;
  bool _alertOpen = false;
  String _lastAlertedId = '';

  static const _destinations = [
    (icon: Icons.doorbell_outlined, active: Icons.doorbell, label: 'Door'),
    (icon: Icons.videocam_outlined, active: Icons.videocam, label: 'Live'),
    (icon: Icons.history_outlined, active: Icons.history, label: 'History'),
    (icon: Icons.groups_outlined, active: Icons.groups, label: 'People'),
    (icon: Icons.settings_outlined, active: Icons.settings, label: 'Settings'),
  ];

  void _handleWs(Map<String, dynamic> msg) {
    final type = asStr(msg['type']);
    switch (type) {
      case 'event':
        final ev = VisitorEvent.fromJson(asMap(msg['event']));
        ref.read(latestEventProvider.notifier).set(ev);
        ref.invalidate(historyProvider);
        _maybeAlert(ev);
      case 'event_update':
        // The background VLM enrich finished: the event now carries the full
        // scene description. Push it to the phone as text + speech + a large
        // vibration so a Blind or Deaf user actually receives it (Bug 5).
        final ev = VisitorEvent.fromJson(asMap(msg['event']));
        ref.read(latestEventProvider.notifier).set(ev);
        ref.invalidate(historyProvider);
        _onEnriched(ev);
      case 'history_update':
        ref.invalidate(historyProvider);
      case 'known_update':
        ref.invalidate(knownProvider);
      case 'visitor_speech':
        // The visitor's speech was transcribed onto an existing event; refresh
        // history so the transcript shows. Latest card updates on next reload.
        ref.invalidate(historyProvider);
      default:
        break; // ignore unknown / suggestions_update / voice
    }
  }

  String _lastEnrichedId = '';

  /// Deliver the enriched VLM description on the PHONE: large vibration first
  /// (Deaf/Both feel it), then the text on screen, then speech (Blind/Both).
  /// Once per event id — repeat enrich broadcasts don't re-buzz the user.
  Future<void> _onEnriched(VisitorEvent ev) async {
    if (ev.eventId.isEmpty || ev.eventId == _lastEnrichedId) return;
    final description = [
      ev.announcementText.trim().isNotEmpty
          ? ev.announcementText.trim()
          : ev.sceneSummary.trim(),
      if (ev.ocrText.trim().isNotEmpty) 'Visible text: ${ev.ocrText.trim()}',
    ].where((s) => s.isNotEmpty).join(' ');
    if (description.isEmpty) return;
    _lastEnrichedId = ev.eventId;

    final mode = ref.read(modeProvider);
    final audio = ref.read(audioProvider);
    await audio.descriptionVibrate();
    if (mounted) showSnack(context, description);
    if (mode == 'blind' || mode == 'both') {
      await audio.speak(description, ref.read(apiProvider));
    } else {
      audio.announceOnly(description);
    }
  }

  Future<void> _maybeAlert(VisitorEvent ev) async {
    // Skip our own test ring and duplicates.
    final selfId = ref.read(selfTriggeredIdProvider);
    if (selfId != null && selfId == ev.eventId) {
      ref.read(selfTriggeredIdProvider.notifier).set(null);
      return;
    }
    if (_alertOpen || ev.eventId == _lastAlertedId) return;
    _lastAlertedId = ev.eventId;
    _alertOpen = true;
    try {
      // A leading earcon classifies the visitor before any words: the warning
      // chime for a spoof, the success chime for a recognised person, the
      // signature ding-dong otherwise (speech follows it).
      final earcon = switch (ev.kind) {
        'spoof' => Sfx.error,
        'known' => Sfx.success,
        _ => Sfx.doorbell,
      };
      await ref.read(audioProvider).sfx(earcon);
      if (!mounted) return;
      await DoorbellAlert.show(
        context,
        event: ev,
        mode: ref.read(modeProvider),
        audio: ref.read(audioProvider),
        api: ref.read(apiProvider),
      );
    } finally {
      _alertOpen = false;
    }
  }

  @override
  Widget build(BuildContext context) {
    // Drive the live-alert dispatcher from the persistent event stream.
    ref.listen(eventStreamProvider, (prev, next) {
      final msg = next.value;
      if (msg != null) _handleWs(msg);
    });

    return Scaffold(
      extendBody: true, // screens scroll under the floating glass nav
      body: IndexedStack(
        index: _index,
        children: const [
          HomeScreen(),
          LiveScreen(),
          HistoryScreen(),
          PeopleScreen(),
          SettingsScreen(),
        ],
      ),
      bottomNavigationBar: _GlassNavBar(
        index: _index,
        destinations: _destinations,
        onSelect: (i) => setState(() => _index = i),
      ),
    );
  }
}

/// Floating frosted-glass navigation pill: blurred, top-lit, softly shadowed,
/// hovering above the content. Built on Material's [NavigationBar] so all its
/// accessibility behaviour (semantics, tooltips, 48dp targets) is preserved.
class _GlassNavBar extends StatelessWidget {
  const _GlassNavBar({
    required this.index,
    required this.destinations,
    required this.onSelect,
  });

  final int index;
  final List<({IconData icon, IconData active, String label})> destinations;
  final ValueChanged<int> onSelect;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final r = BorderRadius.circular(T.rLg);
    return SafeArea(
      top: false,
      minimum: const EdgeInsets.fromLTRB(T.s12, 0, T.s12, T.s12),
      child: Container(
        decoration: BoxDecoration(
          borderRadius: r,
          gradient: LinearGradient(
            begin: Alignment.topLeft,
            end: Alignment.bottomRight,
            colors: [
              Colors.white.withValues(alpha: 0.22),
              Colors.white.withValues(alpha: 0.04),
            ],
          ),
          boxShadow: [
            BoxShadow(
              color: Colors.black.withValues(alpha: 0.45),
              blurRadius: 28,
              offset: const Offset(0, 12),
              spreadRadius: -10,
            ),
          ],
        ),
        padding: const EdgeInsets.all(1),
        child: ClipRRect(
          borderRadius: r,
          child: BackdropFilter(
            filter: ImageFilter.blur(sigmaX: 24, sigmaY: 24),
            child: DecoratedBox(
              decoration: BoxDecoration(
                borderRadius: r,
                color: cs.surfaceContainer.withValues(alpha: 0.55),
              ),
              child: NavigationBar(
                selectedIndex: index,
                onDestinationSelected: onSelect,
                backgroundColor: Colors.transparent,
                destinations: [
                  for (final d in destinations)
                    NavigationDestination(
                      icon: Icon(d.icon),
                      selectedIcon: Icon(d.active),
                      label: d.label,
                    ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}
