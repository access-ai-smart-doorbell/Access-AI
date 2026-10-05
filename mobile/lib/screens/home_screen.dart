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
import 'event_detail_screen.dart';
import 'voice_screen.dart';

/// Home / "Door" — the signature screen. Matches the reference design:
/// AccessAI header with mic button + offline badge, animated doorbell hero,
/// "Someone is at the door" title, mode selector (Visual + Audio / Visual Only /
/// Audio Only), Ring & Hear Visitor action cards, Latest Event with rich
/// camera card showing person detection.
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
            shown.isEmpty ? 'No clear speech was heard.' : 'Heard: "$shown"');
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
      if (mounted) showSnack(context, 'Spoke: "$text"');
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

    // Prefer the freshest event we've seen (from a ring or a live push),
    // otherwise the newest from history.
    final historyList = history.asData?.value ?? const <VisitorEvent>[];
    final VisitorEvent? event =
        latest ?? (historyList.isNotEmpty ? historyList.first : null);

    return Scaffold(
      backgroundColor: Colors.transparent,
      body: MeshScaffoldBody(
        child: SafeArea(
          bottom: false,
          child: ContentWidth(
            child: RefreshIndicator(
              onRefresh: () async {
                ref.invalidate(historyProvider);
                await ref.read(historyProvider.future);
              },
              child: ListView(
                padding: const EdgeInsets.fromLTRB(T.s16, T.s12, T.s16, 120),
                children: [
                  // ── Header ──────────────────────────────────────
                  Entrance(
                    index: 0,
                    child: _buildHeader(wsState),
                  ),
                  const SizedBox(height: T.s24),

                  // ── Doorbell hero ───────────────────────────────
                  Entrance(
                    index: 1,
                    child: Center(
                      child: ParallaxTilt(
                        strength: 8,
                        child: DoorbellHero(
                          size: 180,
                          active: _ringing || _hearing,
                          semanticLabel: 'AccessAI smart doorbell, mode '
                              '${modeShort(mode)}',
                        ),
                      ),
                    ),
                  ),
                  const SizedBox(height: T.s16),

                  // ── Title ───────────────────────────────────────
                  Entrance(
                    index: 2,
                    child: Column(
                      children: [
                        Text(
                          event != null && event.totalPeople > 0
                              ? 'Someone is at the door'
                              : 'No one at the door',
                          style: text.headlineSmall?.copyWith(
                            fontWeight: FontWeight.w700,
                            color: T.textPrimary,
                          ),
                          textAlign: TextAlign.center,
                        ),
                        const SizedBox(height: T.s4),
                        Text(
                          event != null
                              ? 'Today • ${_shortTime(event.timestamp)}'
                              : 'Waiting for activity',
                          style: text.bodyMedium?.copyWith(
                            color: T.textTertiary,
                          ),
                          textAlign: TextAlign.center,
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(height: T.s20),

                  // ── Mode selector ───────────────────────────────
                  Entrance(
                    index: 3,
                    child: _buildModeSelector(mode),
                  ),
                  const SizedBox(height: T.s16),

                  // ── Action buttons ──────────────────────────────
                  Entrance(index: 4, child: _primaryActions()),
                  if (_hearing) ...[
                    const SizedBox(height: T.s12),
                    _listeningIndicator(),
                  ],
                  const SizedBox(height: T.s24),

                  // ── Latest Event ────────────────────────────────
                  Entrance(
                    index: 5,
                    child: Row(
                      children: [
                        Text('Latest Event',
                            style: text.titleLarge?.copyWith(
                                color: T.textPrimary,
                                fontWeight: FontWeight.w700)),
                        const Spacer(),
                        TextButton(
                          onPressed: () {},
                          child: Text('View all events',
                              style: text.labelMedium?.copyWith(
                                  color: T.textTertiary)),
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(height: T.s8),
                  if (event != null)
                    Entrance(
                      index: 6,
                      child: EventCard(
                        event: event,
                        onTap: () => Navigator.of(context).push(
                          MaterialPageRoute(
                            builder: (_) => EventDetailScreen(event: event),
                          ),
                        ),
                      )
                          .animate(target: context.reduceMotion ? 0 : 1)
                          .fadeIn(duration: T.med)
                          .slideY(begin: 0.06, end: 0),
                    )
                  else
                    Entrance(index: 6, child: _emptyLatest(history)),

                  const SizedBox(height: T.s24),

                  // ── Reply composer ──────────────────────────────
                  Entrance(
                    index: 7,
                    child: GlassCard(
                      child: ReplyComposer(
                        onSend: _reply,
                        sending: _sendingReply,
                        quickReplies: ref.watch(quickRepliesProvider).value ??
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

  /// Header matching reference: AccessAI logo + title + subtitle on left,
  /// large blue mic button + offline badge on right.
  Widget _buildHeader(WsState? wsState) {
    final text = Theme.of(context).textTheme;
    return Row(
      crossAxisAlignment: CrossAxisAlignment.center,
      children: [
        // Logo
        Container(
          width: 44,
          height: 44,
          decoration: BoxDecoration(
            color: T.primary,
            borderRadius: BorderRadius.circular(T.s12),
          ),
          child: const Center(
            child: Text('A',
                style: TextStyle(
                  color: Colors.white,
                  fontSize: 22,
                  fontWeight: FontWeight.w800,
                )),
          ),
        ),
        const SizedBox(width: T.s12),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text('AccessAI',
                  style: text.titleLarge?.copyWith(
                    fontWeight: FontWeight.w800,
                    color: T.textPrimary,
                    fontSize: 20,
                  )),
              Text('Your Accessible Doorbell Assistant',
                  style: text.bodySmall?.copyWith(
                    color: T.textTertiary,
                    fontSize: 12,
                  )),
            ],
          ),
        ),
        // Mic button
        GestureDetector(
          onTap: () => openVoice(context),
          child: Container(
            width: 44,
            height: 44,
            decoration: BoxDecoration(
              color: T.primary,
              shape: BoxShape.circle,
              boxShadow: [
                BoxShadow(
                  color: T.primary.withValues(alpha: 0.25),
                  blurRadius: 12,
                  offset: const Offset(0, 4),
                ),
              ],
            ),
            child: const Icon(Icons.mic, color: Colors.white, size: 22),
          ),
        ),
        const SizedBox(width: T.s8),
        // Connection badge
        _connPill(wsState),
      ],
    );
  }

  /// Mode selector matching reference: "Mode: Both" header with three
  /// toggle buttons (Visual + Audio, Visual Only, Audio Only).
  Widget _buildModeSelector(String mode) {
    final text = Theme.of(context).textTheme;

    return GlassCard(
      padding: const EdgeInsets.all(T.s16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Text('Mode: ${modeShort(mode)}',
                  style: text.titleMedium?.copyWith(
                    fontWeight: FontWeight.w700,
                    color: T.textPrimary,
                  )),
              const Spacer(),
              Icon(Icons.keyboard_arrow_down, color: T.textTertiary, size: 24),
            ],
          ),
          const SizedBox(height: T.s12),
          Row(
            children: [
              Expanded(
                child: _ModeChip(
                  label: 'Visual + Audio',
                  icons: [Icons.visibility, Icons.volume_up],
                  selected: mode == 'both',
                  onTap: () => ref.read(modeProvider.notifier).set('both'),
                ),
              ),
              const SizedBox(width: T.s8),
              Expanded(
                child: _ModeChip(
                  label: 'Visual Only',
                  icons: [Icons.visibility],
                  selected: mode == 'deaf',
                  onTap: () => ref.read(modeProvider.notifier).set('deaf'),
                ),
              ),
              const SizedBox(width: T.s8),
              Expanded(
                child: _ModeChip(
                  label: 'Audio Only',
                  icons: [Icons.volume_up],
                  selected: mode == 'blind',
                  onTap: () => ref.read(modeProvider.notifier).set('blind'),
                ),
              ),
            ],
          ),
        ],
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
            bgColor: T.accentBg,
            busy: _ringing,
            onTap: _ring,
          ),
        ),
        const SizedBox(width: T.s12),
        Expanded(
          child: _BigActionButton(
            label: 'Hear Visitor',
            sublabel: 'Listen & translate',
            icon: Icons.volume_up,
            color: T.primary,
            bgColor: T.hearBg,
            busy: _hearing,
            onTap: _hear,
          ),
        ),
      ],
    );
  }

  Widget _listeningIndicator() {
    return Semantics(
      liveRegion: true,
      label: 'Listening to the visitor, $_hearSeconds seconds',
      child: GlassCard(
        borderTint: T.known,
        glow: true,
        tint: T.knownBg,
        child: Row(
          children: [
            SizedBox(
                width: 22,
                height: 22,
                child: CircularProgressIndicator(
                    strokeWidth: 3, color: T.known)),
            const SizedBox(width: T.s16),
            Expanded(
              child: Text('Listening to the visitor… ${_hearSeconds}s',
                  style: Theme.of(context)
                      .textTheme
                      .titleMedium
                      ?.copyWith(color: T.textPrimary)),
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
              Icon(Icons.cloud_off, color: T.textTertiary),
              const SizedBox(width: T.s12),
              Expanded(child: Text('$error',
                  style: TextStyle(color: T.textSecondary))),
            ],
          ),
        AsyncData() => Padding(
            padding: const EdgeInsets.symmetric(vertical: T.s8),
            child: Text('No visits yet. Press Ring to test the doorbell.',
                style: TextStyle(color: T.textSecondary)),
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
      WsState.connected => StatusPill(
          label: 'Live',
          color: T.success,
          icon: Icons.wifi,
          semanticLabel: 'Connected — real-time alerts active'),
      WsState.connecting => const StatusPill(
          label: 'Connecting', color: T.accent, icon: Icons.wifi_find),
      _ => StatusPill(
          label: 'Offline • Local AI',
          color: T.danger,
          icon: Icons.wifi_off,
          semanticLabel: 'Offline — AI processing running locally'),
    };
  }

  String _shortTime(String iso) {
    final dt = DateTime.tryParse(iso);
    if (dt == null) return iso;
    final local = dt.toLocal();
    final hh = local.hour % 12 == 0 ? 12 : local.hour % 12;
    final mm = local.minute.toString().padLeft(2, '0');
    final ap = local.hour < 12 ? 'AM' : 'PM';
    return '$hh:$mm $ap';
  }
}

/// Mode toggle chip matching the reference: soft blue active state with
/// selected styling.
class _ModeChip extends StatelessWidget {
  const _ModeChip({
    required this.label,
    required this.icons,
    required this.selected,
    required this.onTap,
  });

  final String label;
  final List<IconData> icons;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: AnimatedContainer(
        duration: T.fast,
        padding: const EdgeInsets.symmetric(vertical: T.s10, horizontal: T.s8),
        decoration: BoxDecoration(
          color: selected ? T.primaryLight : T.bgSubtle,
          borderRadius: BorderRadius.circular(T.rMd),
          border: Border.all(
            color: selected ? T.primary.withValues(alpha: 0.3) : T.border,
          ),
        ),
        child: Column(
          children: [
            Row(
              mainAxisAlignment: MainAxisAlignment.center,
              mainAxisSize: MainAxisSize.min,
              children: [
                for (int i = 0; i < icons.length; i++) ...[
                  if (i > 0) const SizedBox(width: T.s4),
                  Icon(icons[i],
                      size: 18,
                      color: selected ? T.primary : T.textTertiary),
                ],
              ],
            ),
            const SizedBox(height: T.s4),
            Text(
              label,
              style: TextStyle(
                fontSize: 11,
                fontWeight: selected ? FontWeight.w600 : FontWeight.w500,
                color: selected ? T.primary : T.textSecondary,
              ),
              textAlign: TextAlign.center,
            ),
          ],
        ),
      ),
    );
  }
}

/// A premium action tile matching the reference: soft colored background,
/// icon, label + sublabel, chevron arrow. Clean and accessible.
class _BigActionButton extends StatefulWidget {
  const _BigActionButton({
    required this.label,
    required this.sublabel,
    required this.icon,
    required this.color,
    required this.bgColor,
    required this.busy,
    required this.onTap,
  });

  final String label;
  final String sublabel;
  final IconData icon;
  final Color color;
  final Color bgColor;
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
            constraints: const BoxConstraints(minHeight: 80),
            padding: const EdgeInsets.all(T.s16),
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(T.rLg),
              color: widget.bgColor,
              border: Border.all(
                color: widget.color.withValues(alpha: 0.15),
              ),
              boxShadow: [
                BoxShadow(
                  color: widget.color.withValues(alpha: 0.08),
                  blurRadius: 12,
                  offset: const Offset(0, 4),
                ),
              ],
            ),
            child: Row(
              children: [
                // Icon circle
                Container(
                  width: 40,
                  height: 40,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    color: widget.color.withValues(alpha: 0.15),
                  ),
                  child: widget.busy
                      ? Padding(
                          padding: const EdgeInsets.all(10),
                          child: CircularProgressIndicator(
                              strokeWidth: 2.5, color: widget.color),
                        )
                      : Icon(widget.icon, size: 20, color: widget.color),
                ),
                const SizedBox(width: T.s12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(widget.label,
                          style: text.titleMedium?.copyWith(
                              fontWeight: FontWeight.w700,
                              color: T.textPrimary,
                              fontSize: 16)),
                      Text(widget.sublabel,
                          style: text.bodySmall?.copyWith(
                              color: T.textTertiary,
                              fontSize: 12)),
                    ],
                  ),
                ),
                Icon(Icons.chevron_right,
                    color: T.textTertiary, size: 22),
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
