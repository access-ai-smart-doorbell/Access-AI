import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../core/parse.dart';
import '../core/tokens.dart';
import '../core/ui.dart';
import '../models/visitor_event.dart';
import '../services/audio_service.dart';
import '../state/providers.dart';
import '../widgets/alert_overlay.dart';
import 'clips_screen.dart';
import 'history_screen.dart';
import 'home_screen.dart';
import 'live_screen.dart';
import 'people_screen.dart';
import 'settings_screen.dart';

/// The app's home scaffold: a clean white bottom navigation bar with five tabs.
/// Matches the reference design: simple outlined Material icons, selected tab
/// has a soft blue pill background with blue icon/text, unselected tabs are
/// gray. It also hosts the ALWAYS-ON live-alert listener.
class NavShell extends ConsumerStatefulWidget {
  const NavShell({super.key});

  @override
  ConsumerState<NavShell> createState() => _NavShellState();
}

class _NavShellState extends ConsumerState<NavShell> with WidgetsBindingObserver {
  int _index = 0;
  bool _alertOpen = false;
  String _lastAlertedId = '';

  /// Tracks foreground/background so an event can choose between the in-app
  /// takeover and a system notification. Seeded from the binding rather than
  /// assumed 'resumed': the app can be launched straight into the background.
  AppLifecycleState _lifecycle = AppLifecycleState.resumed;

  /// True when the user is not looking at the app, so an alert must go to the
  /// notification shade instead of the screen.
  bool get _backgrounded => _lifecycle != AppLifecycleState.resumed;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _lifecycle =
        WidgetsBinding.instance.lifecycleState ?? AppLifecycleState.resumed;
    // Touch the notifier so a user who left background alerts on gets the
    // foreground service (and therefore a live socket) without visiting
    // Settings first.
    Future.microtask(() => ref.read(backgroundAlertsProvider));
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    _lifecycle = state;
  }

  static const _destinations = [
    (icon: Icons.home_outlined, active: Icons.home, label: 'Door'),
    (icon: Icons.videocam_outlined, active: Icons.videocam, label: 'Live'),
    (icon: Icons.video_library_outlined, active: Icons.video_library, label: 'Clips'),
    (icon: Icons.access_time, active: Icons.access_time_filled, label: 'History'),
    (icon: Icons.person_outline, active: Icons.person, label: 'People'),
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
      ev.sceneSummary.trim(),
      if (ev.ocrText.trim().isNotEmpty) 'Visible text: ${ev.ocrText.trim()}',
    ].where((s) => s.isNotEmpty).join(' ');
    if (description.isEmpty) return;
    _lastEnrichedId = ev.eventId;

    // Backgrounded: replace the original shade alert with the fuller VLM
    // description (same notification id, onlyAlertOnce) rather than buzzing a
    // second time for the same visitor.
    if (_backgrounded) {
      await ref.read(notificationServiceProvider).showEnriched(ev, description);
      return;
    }

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

    // Backgrounded: there is no screen to take over, and speaking out of
    // nowhere is worse than useless. The alert goes to the notification shade
    // instead -- Android rings and vibrates it on the max-importance doorbell
    // channel, which is what actually reaches the user with the app closed.
    if (_backgrounded) {
      await ref.read(notificationServiceProvider).showEvent(ev);
      return;
    }

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
      body: IndexedStack(
        index: _index,
        children: const [
          HomeScreen(),
          LiveScreen(),
          ClipsScreen(),
          HistoryScreen(),
          PeopleScreen(),
          SettingsScreen(),
        ],
      ),
      bottomNavigationBar: _CleanNavBar(
        index: _index,
        destinations: _destinations,
        onSelect: (i) => setState(() => _index = i),
      ),
    );
  }
}

/// Clean white bottom navigation bar matching the reference design:
/// simple outlined Material icons, selected tab has a soft blue pill
/// background with blue icon and text, unselected tabs are dark gray.
class _CleanNavBar extends StatelessWidget {
  const _CleanNavBar({
    required this.index,
    required this.destinations,
    required this.onSelect,
  });

  final int index;
  final List<({IconData icon, IconData active, String label})> destinations;
  final ValueChanged<int> onSelect;

  @override
  Widget build(BuildContext context) {
    return Container(
      decoration: BoxDecoration(
        color: Colors.white,
        border: Border(
          top: BorderSide(color: T.border.withValues(alpha: 0.5), width: 1),
        ),
        boxShadow: [
          BoxShadow(
            color: Colors.black.withValues(alpha: 0.04),
            blurRadius: 8,
            offset: const Offset(0, -2),
          ),
        ],
      ),
      child: SafeArea(
        top: false,
        child: Padding(
          padding: const EdgeInsets.symmetric(vertical: T.s8),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.spaceAround,
            children: [
              for (int i = 0; i < destinations.length; i++)
                _NavItem(
                  icon: destinations[i].icon,
                  activeIcon: destinations[i].active,
                  label: destinations[i].label,
                  selected: i == index,
                  onTap: () => onSelect(i),
                ),
            ],
          ),
        ),
      ),
    );
  }
}

class _NavItem extends StatelessWidget {
  const _NavItem({
    required this.icon,
    required this.activeIcon,
    required this.label,
    required this.selected,
    required this.onTap,
  });

  final IconData icon;
  final IconData activeIcon;
  final String label;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      behavior: HitTestBehavior.opaque,
      child: Semantics(
        button: true,
        label: label,
        selected: selected,
        child: SizedBox(
          width: 64,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              AnimatedContainer(
                duration: T.fast,
                padding: const EdgeInsets.symmetric(
                    horizontal: T.s16, vertical: T.s6),
                decoration: BoxDecoration(
                  color: selected ? T.primaryLight : Colors.transparent,
                  borderRadius: BorderRadius.circular(T.rPill),
                ),
                child: Icon(
                  selected ? activeIcon : icon,
                  size: 24,
                  color: selected ? T.primary : T.textTertiary,
                ),
              ),
              const SizedBox(height: T.s2),
              Text(
                label,
                style: TextStyle(
                  fontSize: 11,
                  fontWeight: selected ? FontWeight.w600 : FontWeight.w500,
                  color: selected ? T.primary : T.textTertiary,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
