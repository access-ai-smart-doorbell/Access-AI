import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:video_player/video_player.dart';

import '../core/format.dart';
import '../core/motion.dart';
import '../core/tokens.dart';
import '../core/ui.dart';
import '../models/visitor_event.dart';
import '../services/api_service.dart';
import '../services/events_service.dart';
import '../state/providers.dart';
import '../widgets/status_pill.dart';

/// Full detail for one visitor event, matching the reference design:
/// Header with back button + AccessAI + Door Event + Offline badge,
/// large camera image with detection bounding box overlay,
/// person identity card, "What's happening" scene description,
/// objects detected pills, "Visitor said" speech card with speaker button,
/// and Repeat / Dismiss action buttons at the bottom.
class EventDetailScreen extends ConsumerStatefulWidget {
  const EventDetailScreen({super.key, required this.event, this.heroTag});

  final VisitorEvent event;
  final String? heroTag;

  @override
  ConsumerState<EventDetailScreen> createState() => _EventDetailScreenState();
}

class _EventDetailScreenState extends ConsumerState<EventDetailScreen> {
  bool _deleting = false;

  Future<void> _speak() async {
    final e = widget.event;
    final text = e.announcementText.trim().isNotEmpty
        ? e.announcementText
        : '${e.countLine}. ${e.sceneSummary}';
    await ref.read(audioProvider).speak(text, ref.read(apiProvider));
  }

  void _playClip(BuildContext context) {
    final api = ref.read(apiProvider);
    final e = widget.event;
    final url = api.eventClipUrl(e.eventId);
    Navigator.of(context).push(MaterialPageRoute(
      builder: (_) => _EventVideoPlayerScreen(
        url: url,
        eventId: e.eventId,
      ),
    ));
  }

  Future<void> _delete() async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Delete this visit?'),
        content: const Text(
            'This removes the event and its saved snapshot. It cannot be undone.'),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(ctx, false),
              child: const Text('Cancel')),
          FilledButton(
            style: FilledButton.styleFrom(backgroundColor: T.danger),
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Delete'),
          ),
        ],
      ),
    );
    if (confirmed != true) return;
    setState(() => _deleting = true);
    try {
      await ref.read(apiProvider).deleteEvent(widget.event.eventId);
      ref.invalidate(historyProvider);
      if (mounted) {
        showSnack(context, 'Visit deleted');
        Navigator.of(context).pop();
      }
    } on ApiException catch (e) {
      if (mounted) showSnack(context, e.message, error: true);
    } finally {
      if (mounted) setState(() => _deleting = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final e = widget.event;
    final api = ref.watch(apiProvider);
    final wsState = ref.watch(wsStateProvider).value;
    final text = Theme.of(context).textTheme;

    final people = e.people;
    final hasKnown = e.knownCount > 0;
    final hasMultiple = e.totalPeople > 1;

    // Determine the event title
    String title;
    if (e.totalPeople <= 0) {
      title = 'No person detected';
    } else if (hasMultiple) {
      title = '${e.totalPeople} people detected';
    } else if (hasKnown) {
      title = "Someone's at the door";
    } else {
      title = 'Unknown person detected';
    }



    return Scaffold(
      backgroundColor: T.bg,
      body: MeshScaffoldBody(
        child: SafeArea(
          bottom: false,
          child: Column(
            children: [
              // ── Header ────────────────────────────────────────
              Padding(
                padding: const EdgeInsets.symmetric(
                    horizontal: T.s16, vertical: T.s8),
                child: Row(
                  children: [
                    // Back button
                    GestureDetector(
                      onTap: () => Navigator.of(context).pop(),
                      child: Container(
                        width: 40,
                        height: 40,
                        decoration: BoxDecoration(
                          shape: BoxShape.circle,
                          color: T.bgSubtle,
                          border: Border.all(color: T.border),
                        ),
                        child: const Icon(Icons.arrow_back,
                            size: 20, color: T.textPrimary),
                      ),
                    ),
                    const SizedBox(width: T.s12),
                    // Logo
                    Container(
                      width: 36,
                      height: 36,
                      decoration: BoxDecoration(
                        color: T.primary,
                        borderRadius: BorderRadius.circular(T.s8),
                      ),
                      child: const Center(
                        child: Text('A',
                            style: TextStyle(
                              color: Colors.white,
                              fontSize: 18,
                              fontWeight: FontWeight.w800,
                            )),
                      ),
                    ),
                    const SizedBox(width: T.s8),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text('AccessAI',
                              style: text.titleMedium?.copyWith(
                                fontWeight: FontWeight.w800,
                                color: T.textPrimary,
                                fontSize: 17,
                              )),
                          Text('Door Event',
                              style: text.bodySmall?.copyWith(
                                color: T.textTertiary,
                                fontSize: 12,
                              )),
                        ],
                      ),
                    ),
                    // Connection badge
                    _connPill(wsState),
                  ],
                ),
              ),

              // ── Scrollable content ────────────────────────────
              Expanded(
                child: ListView(
                  padding: const EdgeInsets.fromLTRB(0, 0, 0, T.s32),
                  children: [
                    // ── Camera image with detection box ──────────
                    if (e.eventId.isNotEmpty)
                      Entrance(
                        index: 0,
                        child: Padding(
                          padding: const EdgeInsets.symmetric(horizontal: 0),
                          child: Stack(
                            children: [
                              AspectRatio(
                                aspectRatio: 4 / 3,
                                child: Image.network(
                                  api.snapshotUrl(e.eventId),
                                  fit: BoxFit.cover,
                                  gaplessPlayback: true,
                                  errorBuilder: (_, _, _) => Container(
                                    color: T.bgSubtle,
                                    child: Center(
                                      child: Column(
                                        mainAxisSize: MainAxisSize.min,
                                        children: [
                                          Icon(
                                              Icons
                                                  .image_not_supported_outlined,
                                              size: 40,
                                              color: T.textTertiary),
                                          const SizedBox(height: T.s8),
                                          Text(
                                              'No snapshot saved for this visit',
                                              style: TextStyle(
                                                  color: T.textTertiary)),
                                        ],
                                      ),
                                    ),
                                  ),
                                  loadingBuilder:
                                      (context, child, progress) =>
                                          progress == null
                                              ? child
                                              : const Center(
                                                  child:
                                                      CircularProgressIndicator()),
                                ),
                              ),
                              // "Door Camera" badge
                              Positioned(
                                left: T.s12,
                                top: T.s12,
                                child: Container(
                                  padding: const EdgeInsets.symmetric(
                                      horizontal: T.s8, vertical: T.s4),
                                  decoration: BoxDecoration(
                                    color:
                                        Colors.black.withValues(alpha: 0.55),
                                    borderRadius:
                                        BorderRadius.circular(T.s6),
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
                                    borderRadius:
                                        BorderRadius.circular(T.s6),
                                  ),
                                  child: Text(
                                    prettyTime(e.timestamp),
                                    style: text.labelSmall?.copyWith(
                                        color: Colors.white,
                                        fontWeight: FontWeight.w600,
                                        fontSize: 11),
                                  ),
                                ),
                              ),
                              // Detection box overlay (simulated)
                              if (people.isNotEmpty)
                                ..._buildDetectionBoxes(people),
                            ],
                          ),
                        ),
                      ),

                    const SizedBox(height: T.s16),

                    // ── Title and status ─────────────────────────
                    Padding(
                      padding:
                          const EdgeInsets.symmetric(horizontal: T.s16),
                      child: Entrance(
                        index: 1,
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            // Title icon + text
                            Row(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                if (hasMultiple)
                                  Container(
                                    width: 44,
                                    height: 44,
                                    decoration: BoxDecoration(
                                      shape: BoxShape.circle,
                                      color: T.multiBg,
                                    ),
                                    child: Icon(Icons.groups,
                                        size: 24, color: T.multi),
                                  )
                                else if (!hasKnown && e.totalPeople > 0)
                                  Container(
                                    width: 44,
                                    height: 44,
                                    decoration: BoxDecoration(
                                      shape: BoxShape.circle,
                                      color: T.unknownBg,
                                    ),
                                    child: Icon(
                                        Icons.notifications_active,
                                        size: 24,
                                        color: T.unknown),
                                  ),
                                if (hasMultiple ||
                                    (!hasKnown && e.totalPeople > 0))
                                  const SizedBox(width: T.s12),
                                Expanded(
                                  child: Column(
                                    crossAxisAlignment:
                                        CrossAxisAlignment.start,
                                    children: [
                                      Text(title,
                                          style: text.headlineSmall
                                              ?.copyWith(
                                            fontWeight: FontWeight.w700,
                                            color: T.textPrimary,
                                          )),
                                      // Status badge
                                      if (!hasKnown && e.totalPeople > 0)
                                        Padding(
                                          padding: const EdgeInsets.only(
                                              top: T.s4),
                                          child: StatusPill(
                                            label: 'Unknown',
                                            color: T.unknown,
                                            icon: Icons
                                                .warning_amber_rounded,
                                          ),
                                        ),
                                      const SizedBox(height: T.s4),
                                      Text(
                                          'Today • ${_shortTime(e.timestamp)}',
                                          style: text.bodyMedium?.copyWith(
                                              color: T.textTertiary)),
                                    ],
                                  ),
                                ),
                              ],
                            ),
                          ],
                        ),
                      ),
                    ),

                    const SizedBox(height: T.s16),

                    // ── Person identity cards ───────────────────
                    if (hasMultiple)
                      // Multiple people: horizontal cards
                      Padding(
                        padding:
                            const EdgeInsets.symmetric(horizontal: T.s16),
                        child: Entrance(
                          index: 2,
                          child: SizedBox(
                            height: 140,
                            child: ListView.separated(
                              scrollDirection: Axis.horizontal,
                              itemCount: people.length,
                              separatorBuilder: (_, _) =>
                                  const SizedBox(width: T.s12),
                              itemBuilder: (_, i) =>
                                  _PersonCard(person: people[i], index: i + 1),
                            ),
                          ),
                        ),
                      )
                    else if (people.isNotEmpty)
                      Padding(
                        padding:
                            const EdgeInsets.symmetric(horizontal: T.s16),
                        child: Entrance(
                          index: 2,
                          child: _SinglePersonCard(person: people.first),
                        ),
                      )
                    else
                      Padding(
                        padding:
                            const EdgeInsets.symmetric(horizontal: T.s16),
                        child: Entrance(
                          index: 2,
                          child: _NoPersonCard(),
                        ),
                      ),

                    const SizedBox(height: T.s20),

                    // ── What's happening ─────────────────────────
                    if (e.sceneSummary.isNotEmpty)
                      Padding(
                        padding:
                            const EdgeInsets.symmetric(horizontal: T.s16),
                        child: Entrance(
                          index: 3,
                          child: _SectionCard(
                            icon: Icons.description_outlined,
                            title: "What's happening",
                            child: Text(e.sceneSummary,
                                style: text.bodyMedium?.copyWith(
                                    color: T.textSecondary,
                                    height: 1.5)),
                          ),
                        ),
                      ),

                    // ── Objects detected ─────────────────────────
                    if (e.carriedObjects.isNotEmpty) ...[
                      const SizedBox(height: T.s16),
                      Padding(
                        padding:
                            const EdgeInsets.symmetric(horizontal: T.s16),
                        child: Entrance(
                          index: 4,
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Row(
                                children: [
                                  Icon(Icons.category_outlined,
                                      size: 20, color: T.textTertiary),
                                  const SizedBox(width: T.s8),
                                  Text('Objects detected',
                                      style: text.titleMedium?.copyWith(
                                        fontWeight: FontWeight.w700,
                                        color: T.textPrimary,
                                      )),
                                ],
                              ),
                              const SizedBox(height: T.s12),
                              Wrap(
                                spacing: T.s8,
                                runSpacing: T.s8,
                                children: [
                                  for (final obj in e.carriedObjects)
                                    _ObjectPill(obj),
                                  _ObjectPill('Door'),
                                ],
                              ),
                            ],
                          ),
                        ),
                      ),
                    ],

                    // ── Visitor said ─────────────────────────────
                    if (e.hasSpeech) ...[
                      const SizedBox(height: T.s20),
                      Padding(
                        padding:
                            const EdgeInsets.symmetric(horizontal: T.s16),
                        child: Entrance(
                          index: 5,
                          child: _VisitorSpeechCard(event: e),
                        ),
                      ),
                    ],

                    const SizedBox(height: T.s24),

                    // ── Bottom actions ───────────────────────────
                    Padding(
                      padding:
                          const EdgeInsets.symmetric(horizontal: T.s16),
                      child: Entrance(
                        index: 6,
                        child: Column(
                          children: [
                            // Watch Recording button
                            if (e.eventId.isNotEmpty)
                              Padding(
                                padding: const EdgeInsets.only(bottom: T.s12),
                                child: SizedBox(
                                  width: double.infinity,
                                  child: _ActionButton(
                                    icon: Icons.play_circle_outline,
                                    label: 'Watch Recording',
                                    sublabel: 'Play the event video clip',
                                    color: T.accent,
                                    bgColor: T.bgSubtle,
                                    onTap: () => _playClip(context),
                                  ),
                                ),
                              ),
                            Row(
                              children: [
                                Expanded(
                                  child: _ActionButton(
                                    icon: Icons.volume_up,
                                    label: 'Repeat',
                                    sublabel: 'Play the audio again',
                                    color: T.primary,
                                    bgColor: T.primaryLighter,
                                    onTap: _speak,
                                  ),
                                ),
                                const SizedBox(width: T.s12),
                                Expanded(
                                  child: _ActionButton(
                                    icon: Icons.check_circle,
                                    label: 'Dismiss',
                                    sublabel: 'Mark as seen',
                                    color: T.known,
                                    bgColor: T.knownBg,
                                    onTap: () => Navigator.of(context).pop(),
                                  ),
                                ),
                              ],
                            ),
                          ],
                        ),
                      ),
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  List<Widget> _buildDetectionBoxes(List<Person> people) {
    // Simulate detection box overlays on the image
    final boxes = <Widget>[];
    for (int i = 0; i < people.length && i < 3; i++) {
      final p = people[i];
      final trusted = p.known && !p.isSpoof;
      final color = p.isSpoof
          ? T.danger
          : trusted
              ? T.known
              : T.unknown;
      final label =
          trusted ? '${i + 1}. ${p.name}' : '${i + 1}. Unknown';

      // Position boxes at different locations
      final left = i == 0
          ? 0.10
          : i == 1
              ? 0.35
              : 0.60;
      final top = 0.12;
      final width = 0.30;
      final height = 0.75;

      boxes.add(Positioned.fill(
        child: LayoutBuilder(
          builder: (ctx, constraints) {
            return Stack(
              children: [
                Positioned(
                  left: constraints.maxWidth * left,
                  top: constraints.maxHeight * top,
                  width: constraints.maxWidth * width,
                  height: constraints.maxHeight * height,
                  child: Container(
                    decoration: BoxDecoration(
                      border: Border.all(color: color, width: 2),
                      borderRadius: BorderRadius.circular(4),
                    ),
                  ),
                ),
                Positioned(
                  left: constraints.maxWidth * left,
                  top: constraints.maxHeight * top - 20,
                  child: Container(
                    padding: const EdgeInsets.symmetric(
                        horizontal: 6, vertical: 2),
                    decoration: BoxDecoration(
                      color: color,
                      borderRadius: BorderRadius.circular(4),
                    ),
                    child: Text(label,
                        style: const TextStyle(
                          color: Colors.white,
                          fontSize: 10,
                          fontWeight: FontWeight.w600,
                        )),
                  ),
                ),
              ],
            );
          },
        ),
      ));
    }
    return boxes;
  }

  Widget _connPill(WsState? s) {
    return switch (s) {
      WsState.connected => StatusPill(
          label: 'Live',
          color: T.success,
          icon: Icons.wifi,
          semanticLabel: 'Connected'),
      WsState.connecting => const StatusPill(
          label: 'Connecting', color: T.accent, icon: Icons.wifi_find),
      _ => StatusPill(
          label: 'Offline • Local AI',
          color: T.danger,
          icon: Icons.wifi_off,
          semanticLabel: 'Offline — Local AI'),
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

/// Section card with icon header and content
class _SectionCard extends StatelessWidget {
  const _SectionCard({
    required this.icon,
    required this.title,
    required this.child,
  });

  final IconData icon;
  final String title;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Icon(icon, size: 20, color: T.textTertiary),
            const SizedBox(width: T.s8),
            Expanded(
              child: Text(title,
                  style: text.titleMedium?.copyWith(
                    fontWeight: FontWeight.w700,
                    color: T.textPrimary,
                  )),
            ),
            Icon(Icons.chevron_right, size: 20, color: T.textTertiary),
          ],
        ),
        const SizedBox(height: T.s12),
        Container(
          width: double.infinity,
          padding: const EdgeInsets.all(T.s16),
          decoration: BoxDecoration(
            color: T.bgSubtle,
            borderRadius: BorderRadius.circular(T.rMd),
          ),
          child: child,
        ),
      ],
    );
  }
}

/// Single person identity card (for known/unknown single person events)
class _SinglePersonCard extends StatelessWidget {
  const _SinglePersonCard({required this.person});
  final Person person;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final trusted = person.known && !person.isSpoof;

    return Container(
      padding: const EdgeInsets.all(T.s16),
      decoration: BoxDecoration(
        color: T.bgCard,
        borderRadius: BorderRadius.circular(T.rLg),
        border: Border.all(color: T.border),
        boxShadow: [
          BoxShadow(
            color: T.shadow,
            blurRadius: 8,
            offset: const Offset(0, 2),
          ),
        ],
      ),
      child: Row(
        children: [
          // Avatar
          Container(
            width: 56,
            height: 56,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              color: trusted ? T.knownBg : T.unknownBg,
              border: Border.all(
                color: trusted
                    ? T.known.withValues(alpha: 0.3)
                    : T.unknown.withValues(alpha: 0.3),
                width: 2,
              ),
            ),
            child: Icon(
              trusted ? Icons.person : Icons.person_outline,
              size: 28,
              color: trusted ? T.known : T.unknown,
            ),
          ),
          const SizedBox(width: T.s16),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  trusted ? person.name : 'No matching person found',
                  style: text.titleMedium?.copyWith(
                    fontWeight: FontWeight.w700,
                    color: T.textPrimary,
                    fontSize: 17,
                  ),
                ),
                if (trusted) ...[
                  const SizedBox(height: T.s4),
                  Row(
                    children: [
                      StatusPill(
                        label: 'Known',
                        color: T.known,
                        icon: Icons.check_circle,
                      ),
                      if (person.confidence > 0) ...[
                        const SizedBox(width: T.s8),
                        StatusPill(
                          label: '${(person.confidence * 100).round()}% match',
                          color: T.primary,
                          icon: Icons.bar_chart,
                        ),
                      ],
                    ],
                  ),
                ] else ...[
                  const SizedBox(height: T.s4),
                  Text(
                    'This person is not in your saved people list.',
                    style: text.bodySmall?.copyWith(
                      color: T.textTertiary,
                    ),
                  ),
                ],
              ],
            ),
          ),
        ],
      ),
    );
  }
}

/// Card for when no person is detected
class _NoPersonCard extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return Container(
      padding: const EdgeInsets.all(T.s16),
      decoration: BoxDecoration(
        color: T.bgCard,
        borderRadius: BorderRadius.circular(T.rLg),
        border: Border.all(color: T.border),
      ),
      child: Row(
        children: [
          Container(
            width: 56,
            height: 56,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              color: T.bgSubtle,
            ),
            child: Icon(Icons.person_off, size: 28, color: T.textTertiary),
          ),
          const SizedBox(width: T.s16),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('No person detected',
                    style: text.titleMedium?.copyWith(
                      fontWeight: FontWeight.w700,
                      color: T.textPrimary,
                    )),
                const SizedBox(height: T.s4),
                Text('Area in front of the door is clear.',
                    style: text.bodySmall?.copyWith(color: T.textTertiary)),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

/// Compact person card for multi-person horizontal scroll
class _PersonCard extends StatelessWidget {
  const _PersonCard({required this.person, required this.index});
  final Person person;
  final int index;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final trusted = person.known && !person.isSpoof;

    return Container(
      width: 120,
      padding: const EdgeInsets.all(T.s12),
      decoration: BoxDecoration(
        color: T.bgCard,
        borderRadius: BorderRadius.circular(T.rLg),
        border: Border.all(color: T.border),
        boxShadow: [
          BoxShadow(
            color: T.shadow,
            blurRadius: 8,
            offset: const Offset(0, 2),
          ),
        ],
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Text('$index. ${trusted ? person.name : "Unknown"}',
              style: text.labelLarge?.copyWith(
                fontWeight: FontWeight.w700,
                color: T.textPrimary,
                fontSize: 13,
              ),
              textAlign: TextAlign.center,
              maxLines: 1,
              overflow: TextOverflow.ellipsis),
          const SizedBox(height: T.s8),
          Container(
            width: 48,
            height: 48,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              color: trusted ? T.knownBg : T.unknownBg,
              border: Border.all(
                color: trusted
                    ? T.known.withValues(alpha: 0.3)
                    : T.unknown.withValues(alpha: 0.3),
                width: 2,
              ),
            ),
            child: Icon(
              trusted ? Icons.person : Icons.person_outline,
              size: 24,
              color: trusted ? T.known : T.unknown,
            ),
          ),
          const SizedBox(height: T.s8),
          StatusPill(
            label: trusted ? 'Known' : 'Unknown',
            color: trusted ? T.known : T.unknown,
            icon: trusted ? Icons.check_circle : Icons.warning_amber_rounded,
          ),
          const SizedBox(height: T.s4),
          Text(
            trusted && person.confidence > 0
                ? '${(person.confidence * 100).round()}% match'
                : 'Identity not\nrecognized',
            style: text.labelSmall?.copyWith(
              color: T.textTertiary,
              fontSize: 10,
            ),
            textAlign: TextAlign.center,
          ),
        ],
      ),
    );
  }
}

/// Object detection pill
class _ObjectPill extends StatelessWidget {
  const _ObjectPill(this.label);
  final String label;

  @override
  Widget build(BuildContext context) {
    final icon = switch (label.toLowerCase()) {
      'person' => Icons.person,
      'person (3)' => Icons.people,
      'package' || 'box' => Icons.inventory_2,
      'door' => Icons.door_front_door,
      'backpack' || 'bag' => Icons.backpack,
      _ => Icons.category,
    };
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: T.s12, vertical: T.s8),
      decoration: BoxDecoration(
        color: T.bgSubtle,
        borderRadius: BorderRadius.circular(T.rPill),
        border: Border.all(color: T.border),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 16, color: T.textSecondary),
          const SizedBox(width: T.s6),
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

/// Visitor speech card with speaker button
class _VisitorSpeechCard extends StatelessWidget {
  const _VisitorSpeechCard({required this.event});
  final VisitorEvent event;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final original = event.speechTranscript.trim();
    final translated = event.translatedTranscript.trim();
    final showTranslated = translated.isNotEmpty && translated != original;
    final shown = showTranslated ? translated : original;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Icon(Icons.record_voice_over, size: 20, color: T.textTertiary),
            const SizedBox(width: T.s8),
            Text('Visitor said',
                style: text.titleMedium?.copyWith(
                  fontWeight: FontWeight.w700,
                  color: T.textPrimary,
                )),
          ],
        ),
        const SizedBox(height: T.s12),
        Container(
          width: double.infinity,
          padding: const EdgeInsets.all(T.s16),
          decoration: BoxDecoration(
            color: T.primaryLighter,
            borderRadius: BorderRadius.circular(T.rLg),
            border: Border.all(color: T.primary.withValues(alpha: 0.12)),
          ),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('"$shown"',
                        style: text.bodyLarge?.copyWith(
                          fontStyle: FontStyle.italic,
                          color: T.textPrimary,
                          height: 1.5,
                          fontSize: 16,
                        )),
                    const SizedBox(height: T.s6),
                    Text(
                      'Detected and ${showTranslated ? "translated" : "transcribed"} (English)',
                      style: text.bodySmall?.copyWith(
                        color: T.textTertiary,
                        fontSize: 12,
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(width: T.s12),
              // Speaker button
              Container(
                width: 48,
                height: 48,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  color: T.primary,
                  boxShadow: [
                    BoxShadow(
                      color: T.primary.withValues(alpha: 0.25),
                      blurRadius: 8,
                      offset: const Offset(0, 2),
                    ),
                  ],
                ),
                child:
                    const Icon(Icons.volume_up, color: Colors.white, size: 22),
              ),
            ],
          ),
        ),
      ],
    );
  }
}

/// Bottom action button (Repeat / Dismiss)
class _ActionButton extends StatelessWidget {
  const _ActionButton({
    required this.icon,
    required this.label,
    required this.sublabel,
    required this.color,
    required this.bgColor,
    required this.onTap,
  });

  final IconData icon;
  final String label;
  final String sublabel;
  final Color color;
  final Color bgColor;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return GestureDetector(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.symmetric(
            vertical: T.s16, horizontal: T.s16),
        decoration: BoxDecoration(
          color: bgColor,
          borderRadius: BorderRadius.circular(T.rLg),
          border: Border.all(color: color.withValues(alpha: 0.15)),
        ),
        child: Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(icon, color: color, size: 22),
            const SizedBox(width: T.s8),
            Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(label,
                    style: text.labelLarge?.copyWith(
                      color: color,
                      fontWeight: FontWeight.w700,
                    )),
                Text(sublabel,
                    style: text.labelSmall?.copyWith(
                      color: color.withValues(alpha: 0.7),
                      fontSize: 10,
                    )),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

// ─── Inline video player for an event clip ───────────────────────────────────

class _EventVideoPlayerScreen extends StatefulWidget {
  const _EventVideoPlayerScreen({
    required this.url,
    required this.eventId,
  });

  final String url;
  final String eventId;

  @override
  State<_EventVideoPlayerScreen> createState() =>
      _EventVideoPlayerScreenState();
}

class _EventVideoPlayerScreenState extends State<_EventVideoPlayerScreen> {
  late VideoPlayerController _controller;
  bool _initialized = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _controller = VideoPlayerController.networkUrl(Uri.parse(widget.url))
      ..initialize().then((_) {
        if (mounted) {
          setState(() => _initialized = true);
          _controller.play();
        }
      }).catchError((e) {
        if (mounted) {
          setState(() => _error = 'Could not load video: $e');
        }
      });
    _controller.addListener(() {
      if (mounted) setState(() {});
    });
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.black,
      appBar: AppBar(
        backgroundColor: Colors.black,
        foregroundColor: Colors.white,
        title: Text('Event ${widget.eventId}',
            style: const TextStyle(
              color: Colors.white,
              fontWeight: FontWeight.w700,
            )),
        leading: IconButton(
          icon: const Icon(Icons.arrow_back),
          onPressed: () => Navigator.of(context).pop(),
        ),
      ),
      body: Center(
        child: _error != null
            ? Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  const Icon(Icons.videocam_off,
                      size: 56, color: Colors.redAccent),
                  const SizedBox(height: T.s16),
                  Padding(
                    padding: const EdgeInsets.symmetric(horizontal: T.s32),
                    child: Text(
                      'No recording available for this event.\n\n$_error',
                      textAlign: TextAlign.center,
                      style: const TextStyle(color: Colors.white70),
                    ),
                  ),
                  const SizedBox(height: T.s16),
                  FilledButton.icon(
                    onPressed: () => Navigator.of(context).pop(),
                    icon: const Icon(Icons.arrow_back),
                    label: const Text('Go back'),
                  ),
                ],
              )
            : !_initialized
                ? Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      const CircularProgressIndicator(color: Colors.white),
                      const SizedBox(height: T.s16),
                      Text('Loading video...',
                          style: TextStyle(
                              color: Colors.white.withValues(alpha: 0.7))),
                    ],
                  )
                : _buildPlayer(),
      ),
    );
  }

  Widget _buildPlayer() {
    final duration = _controller.value.duration;
    final position = _controller.value.position;
    final isPlaying = _controller.value.isPlaying;

    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        AspectRatio(
          aspectRatio: _controller.value.aspectRatio,
          child: VideoPlayer(_controller),
        ),
        const SizedBox(height: T.s16),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: T.s24),
          child: Column(
            children: [
              SliderTheme(
                data: SliderThemeData(
                  activeTrackColor: T.primary,
                  inactiveTrackColor: Colors.white24,
                  thumbColor: Colors.white,
                  thumbShape:
                      const RoundSliderThumbShape(enabledThumbRadius: 7),
                  overlayShape:
                      const RoundSliderOverlayShape(overlayRadius: 14),
                  trackHeight: 3,
                ),
                child: Slider(
                  value: duration.inMilliseconds > 0
                      ? position.inMilliseconds / duration.inMilliseconds
                      : 0,
                  onChanged: (v) {
                    _controller.seekTo(Duration(
                        milliseconds:
                            (v * duration.inMilliseconds).toInt()));
                  },
                ),
              ),
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: T.s8),
                child: Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    Text(_fmt(position),
                        style: const TextStyle(
                            color: Colors.white70, fontSize: 12)),
                    Text(_fmt(duration),
                        style: const TextStyle(
                            color: Colors.white70, fontSize: 12)),
                  ],
                ),
              ),
            ],
          ),
        ),
        const SizedBox(height: T.s8),
        Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            IconButton(
              onPressed: () {
                final target = position - const Duration(seconds: 10);
                _controller.seekTo(
                    target < Duration.zero ? Duration.zero : target);
              },
              icon: const Icon(Icons.replay_10),
              iconSize: 32,
              color: Colors.white,
            ),
            const SizedBox(width: T.s16),
            GestureDetector(
              onTap: () {
                isPlaying ? _controller.pause() : _controller.play();
              },
              child: Container(
                width: 56,
                height: 56,
                decoration: BoxDecoration(
                  color: T.primary,
                  shape: BoxShape.circle,
                ),
                child: Icon(
                  isPlaying ? Icons.pause : Icons.play_arrow,
                  size: 32,
                  color: Colors.white,
                ),
              ),
            ),
            const SizedBox(width: T.s16),
            IconButton(
              onPressed: () {
                final target = position + const Duration(seconds: 10);
                _controller.seekTo(
                    target > duration ? duration : target);
              },
              icon: const Icon(Icons.forward_10),
              iconSize: 32,
              color: Colors.white,
            ),
          ],
        ),
      ],
    );
  }

  String _fmt(Duration d) {
    final m = d.inMinutes.remainder(60).toString().padLeft(2, '0');
    final s = d.inSeconds.remainder(60).toString().padLeft(2, '0');
    return '$m:$s';
  }
}
