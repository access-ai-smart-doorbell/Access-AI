import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_animate/flutter_animate.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../core/format.dart';
import '../core/glass.dart';
import '../core/motion.dart';
import '../core/parse.dart';
import '../core/tokens.dart';
import '../core/ui.dart';
import '../models/visitor_event.dart';
import '../services/api_service.dart';
import '../services/events_service.dart';
import '../state/providers.dart';
import '../widgets/doorbell_hero.dart';
import '../widgets/event_card.dart';
import '../widgets/reply_composer.dart';
import '../widgets/status_pill.dart';
import 'voice_screen.dart';

/// Home / "Door" — the signature screen. Animated doorbell hero, the latest
/// event, and the two clearly-separated primary actions:
///   • Ring  → POST /trigger  (visual-only, fast, records NOTHING)
///   • Hear Visitor → POST /hear_visitor (the ONLY recorder; shows a listening
///     indicator with elapsed seconds)
/// plus a reply composer and an "Ask" shortcut to voice commands.
class HomeScreen extends ConsumerStatefulWidget {
  const HomeScreen({super.key});

  @override
  ConsumerState<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends ConsumerState<HomeScreen> {
  bool _ringing = false;
  bool _hearing = false;
  bool _sendingReply = false;
  int _hearSeconds = 0;
  Timer? _hearTimer;

  @override
  void dispose() {
    _hearTimer?.cancel();
    super.dispose();
  }

  Future<void> _ring() async {
    if (_ringing) return;
    setState(() => _ringing = true);
    final api = ref.read(apiProvider);
    final audio = ref.read(audioProvider);
    try {
      final ev = await api.trigger();
      ref.read(latestEventProvider.notifier).set(ev);
      // Mark this as self-triggered so the live-alert listener doesn't pop a
      // full-screen takeover for our own test ring.
      ref.read(selfTriggeredIdProvider.notifier).set(ev.eventId);
      unawaited(audio.successTap());
      if (mounted) showSnack(context, 'Doorbell rung — ${ev.countLine}');
      ref.invalidate(historyProvider);
    } on ApiException catch (e) {
      if (mounted) showSnack(context, e.message, error: true);
    } finally {
      if (mounted) setState(() => _ringing = false);
    }
  }

  Future<void> _hear() async {
    if (_hearing) return;
    setState(() {
      _hearing = true;
      _hearSeconds = 0;
    });
    ref.read(audioProvider).announceOnly('Listening to the visitor');
    _hearTimer = Timer.periodic(const Duration(seconds: 1), (_) {
      if (mounted) setState(() => _hearSeconds++);
    });
    try {
      final res = await ref.read(apiProvider).hearVisitor();
      final translated = asStr(res['translated']).trim();
      final transcript = asStr(res['transcript']).trim();
      final shown = translated.isNotEmpty ? translated : transcript;
      if (mounted) {
        showSnack(context,
            shown.isEmpty ? 'No clear speech was heard.' : 'Heard: “$shown”');
      }
      // Blind / Both mode must HEAR the visitor's words, not just read them —
      // the snackbar above is the Deaf half, this is the Blind half.
      final mode = ref.read(modeProvider);
      final audio = ref.read(audioProvider);
      if (mode == 'blind' || mode == 'both') {
        final spoken = shown.isEmpty
            ? 'No clear speech was heard.'
            : 'The visitor said: $shown';
        await audio.speak(spoken, ref.read(apiProvider));
      }
      ref.invalidate(historyProvider);
    } on ApiException catch (e) {
      if (mounted) showSnack(context, e.message, error: true);
    } finally {
      _hearTimer?.cancel();
      if (mounted) setState(() => _hearing = false);
    }
  }

  Future<void> _reply(String text) async {
    setState(() => _sendingReply = true);
    try {
      await ref.read(apiProvider).reply(text);
      if (mounted) showSnack(context, 'Spoke: “$text”');
    } on ApiException catch (e) {
      if (mounted) showSnack(context, e.message, error: true);
    } finally {
      if (mounted) setState(() => _sendingReply = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final wsState = ref.watch(wsStateProvider).value;
    final mode = ref.watch(modeProvider);
    final latest = ref.watch(latestEventProvider);
    final history = ref.watch(historyProvider);
    final text = Theme.of(context).textTheme;
    final cs = Theme.of(context).colorScheme;

    // Prefer the freshest event we've seen (from a ring or a live push),
    // otherwise the newest from history.
    final historyList = history.asData?.value ?? const <VisitorEvent>[];
    final VisitorEvent? event =
        latest ?? (historyList.isNotEmpty ? historyList.first : null);

    return Scaffold(
      backgroundColor: Colors.transparent,
      appBar: AppBar(
        title: const Text('AccessAI'),
        actions: [
          IconButton(
            onPressed: () => openVoice(context),
            icon: const Icon(Icons.mic_none),
            tooltip: 'Ask a question',
          ),
          Padding(
            padding: const EdgeInsets.only(right: T.s8),
            child: Center(child: _connPill(wsState)),
          ),
        ],
      ),
      body: MeshScaffoldBody(
        child: SafeArea(
          bottom: false, // content scrolls under the floating glass nav
          child: ContentWidth(
            child: RefreshIndicator(
              onRefresh: () async {
                ref.invalidate(historyProvider);
                await ref.read(historyProvider.future);
              },
              child: ListView(
                padding: const EdgeInsets.fromLTRB(T.s16, T.s16, T.s16, 120),
                children: [
                  Entrance(
                    index: 0,
                    child: Center(
                      child: ParallaxTilt(
                        strength: 8,
                        child: DoorbellHero(
                          size: 200,
                          active: _ringing || _hearing,
                          semanticLabel: 'AccessAI smart doorbell, mode '
                              '${modeShort(mode)}',
                        ),
                      ),
                    ),
                  ),
                  const SizedBox(height: T.s8),
                  Entrance(
                    index: 1,
                    child: Center(
                      child: StatusPill(
                        label: 'Mode: ${modeShort(mode)}',
                        color: T.accent,
                        icon: Icons.accessibility_new,
                        semanticLabel: 'Accessibility mode ${modeLabel(mode)}',
                      ),
                    ),
                  ),
                  const SizedBox(height: T.s24),
                  Entrance(index: 2, child: _primaryActions()),
                  if (_hearing) ...[
                    const SizedBox(height: T.s12),
                    _listeningIndicator(),
                  ],
                  const SizedBox(height: T.s32),
                  Entrance(
                    index: 3,
                    child: Text('Latest at the door',
                        style: text.titleLarge?.copyWith(
                            color: cs.onSurface, letterSpacing: -0.2)),
                  ),
                  const SizedBox(height: T.s12),
                  if (event != null)
                    Entrance(
                      index: 4,
                      child: EventCard(event: event)
                          .animate(target: context.reduceMotion ? 0 : 1)
                          .fadeIn(duration: T.med)
                          .slideY(begin: 0.06, end: 0),
                    )
                  else
                    Entrance(index: 4, child: _emptyLatest(history)),
                  const SizedBox(height: T.s24),
                  Entrance(
                    index: 5,
                    child: GlassCard(
                      child: ReplyComposer(
                        onSend: _reply,
                        sending: _sendingReply,
                        // Server-provided chips (Phase 17); the widget falls
                        // back to its own defaults while loading or offline.
                        quickReplies: ref.watch(quickRepliesProvider).valueOrNull ??
                            ReplyComposer.defaultQuickReplies,
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }

  Widget _primaryActions() {
    return Row(
      children: [
        Expanded(
          child: _BigActionButton(
            label: 'Ring',
            sublabel: 'Announce the door',
            icon: Icons.notifications_active,
            color: T.accent,
            busy: _ringing,
            onTap: _ring,
          ),
        ),
        const SizedBox(width: T.s12),
        Expanded(
          child: _BigActionButton(
            label: 'Hear Visitor',
            sublabel: 'Listen & translate',
            icon: Icons.hearing,
            color: T.seed,
            busy: _hearing,
            onTap: _hear,
          ),
        ),
      ],
    );
  }

  Widget _listeningIndicator() {
    final cs = Theme.of(context).colorScheme;
    return Semantics(
      liveRegion: true,
      label: 'Listening to the visitor, $_hearSeconds seconds',
      child: GlassCard(
        borderTint: T.seed,
        glow: true,
        tint: T.seed.withValues(alpha: 0.35),
        child: Row(
          children: [
            const SizedBox(
                width: 22,
                height: 22,
                child: CircularProgressIndicator(
                    strokeWidth: 3, color: T.seed)),
            const SizedBox(width: T.s16),
            Expanded(
              child: Text('Listening to the visitor… ${_hearSeconds}s',
                  style: Theme.of(context)
                      .textTheme
                      .titleMedium
                      ?.copyWith(color: cs.onSurface)),
            ),
          ],
        ),
      ),
    );
  }

  Widget _emptyLatest(AsyncValue<List<VisitorEvent>> history) {
    return GlassCard(
      child: switch (history) {
        AsyncError(:final error) => Row(
            children: [
              const Icon(Icons.cloud_off),
              const SizedBox(width: T.s12),
              Expanded(child: Text('$error')),
            ],
          ),
        AsyncData() => const Padding(
            padding: EdgeInsets.symmetric(vertical: T.s8),
            child: Text('No visits yet. Press Ring to test the doorbell.'),
          ),
        _ => const Padding(
            padding: EdgeInsets.symmetric(vertical: T.s8),
            child: LinearProgressIndicator(),
          ),
      },
    );
  }

  Widget _connPill(WsState? s) {
    return switch (s) {
      WsState.connected =>
        const StatusPill(label: 'Live', color: T.success, icon: Icons.wifi),
      WsState.connecting => const StatusPill(
          label: 'Connecting', color: T.accent, icon: Icons.wifi_find),
      _ => StatusPill(
          label: 'Offline',
          color: T.danger,
          icon: Icons.wifi_off,
          semanticLabel: 'Not connected to the doorbell'),
    };
  }
}

/// A premium glass action tile: colour-tinted frosted body, specular border,
/// soft glow, press-down scale + haptic. The whole tile is one semantics
/// button; the busy state swaps the icon for a spinner without moving layout.
class _BigActionButton extends StatefulWidget {
  const _BigActionButton({
    required this.label,
    required this.sublabel,
    required this.icon,
    required this.color,
    required this.busy,
    required this.onTap,
  });

  final String label;
  final String sublabel;
  final IconData icon;
  final Color color;
  final bool busy;
  final VoidCallback onTap;

  @override
  State<_BigActionButton> createState() => _BigActionButtonState();
}

class _BigActionButtonState extends State<_BigActionButton> {
  bool _down = false;

  void _set(bool v) {
    if (_down != v && mounted) setState(() => _down = v);
  }

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final cs = Theme.of(context).colorScheme;
    final color = widget.color;

    return Semantics(
      button: true,
      label: '${widget.label}. ${widget.sublabel}',
      enabled: !widget.busy,
      child: GestureDetector(
        behavior: HitTestBehavior.opaque,
        onTapDown: widget.busy
            ? null
            : (_) {
                _set(true);
                HapticFeedback.lightImpact();
              },
        onTapCancel: () => _set(false),
        onTapUp: (_) => _set(false),
        onTap: widget.busy ? null : widget.onTap,
        child: AnimatedScale(
          scale: _down ? 0.96 : 1.0,
          duration: Motion.duration(context, T.fast),
          curve: Motion.curve(context),
          child: Container(
            constraints:
                const BoxConstraints(minHeight: 118),
            padding: const EdgeInsets.all(T.s16),
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(T.rLg),
              gradient: LinearGradient(
                begin: Alignment.topLeft,
                end: Alignment.bottomRight,
                colors: [
                  color.withValues(alpha: 0.26),
                  color.withValues(alpha: 0.10),
                ],
              ),
              border: Border.all(
                  color: color.withValues(alpha: 0.55), width: 1.5),
              boxShadow: [
                BoxShadow(
                  color: color.withValues(alpha: _down ? 0.10 : 0.22),
                  blurRadius: 26,
                  spreadRadius: -6,
                  offset: const Offset(0, 8),
                ),
              ],
            ),
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                widget.busy
                    ? SizedBox(
                        width: 32,
                        height: 32,
                        child: CircularProgressIndicator(
                            strokeWidth: 3, color: color),
                      )
                    : Icon(widget.icon, size: 32, color: color),
                const SizedBox(height: T.s10),
                Text(widget.label,
                    style: text.titleLarge?.copyWith(
                        fontWeight: FontWeight.w800, letterSpacing: -0.3)),
                Text(widget.sublabel,
                    style: text.bodySmall
                        ?.copyWith(color: cs.onSurfaceVariant)),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

/// Small helper used by Home's app bar overflow — opens voice commands.
void openVoice(BuildContext context) {
  Navigator.of(context).push(
    MaterialPageRoute(builder: (_) => const VoiceScreen()),
  );
}
