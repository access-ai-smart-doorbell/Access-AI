import 'package:flutter/material.dart';
import 'package:flutter_animate/flutter_animate.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../core/motion.dart';
import '../core/tokens.dart';
import '../core/ui.dart';
import '../models/visitor_event.dart';
import '../services/api_service.dart';
import '../services/events_service.dart';
import '../state/providers.dart';
import '../widgets/status_pill.dart';
import 'event_detail_screen.dart';

/// History — "Door History" screen matching the reference design:
/// AccessAI header, large "Door History" title with subtitle, date selector,
/// search field, filter pills (All / Known / Unknown / Multiple people),
/// timeline sections (Today / Yesterday) with rich event rows featuring
/// camera thumbnails, time badges, status badges, titles, AI descriptions,
/// and right arrows.
class HistoryScreen extends ConsumerStatefulWidget {
  const HistoryScreen({super.key});

  @override
  ConsumerState<HistoryScreen> createState() => _HistoryScreenState();
}

class _HistoryScreenState extends ConsumerState<HistoryScreen> {
  String _filter = 'all'; // all | known | unknown | multiple

  Future<void> _clear(BuildContext context, WidgetRef ref) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Clear all history?'),
        content: const Text(
            'This deletes every saved visit and its snapshots. Enrolled people '
            'are kept. This cannot be undone.'),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(ctx, false),
              child: const Text('Cancel')),
          FilledButton(
            style: FilledButton.styleFrom(backgroundColor: T.danger),
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Clear all'),
          ),
        ],
      ),
    );
    if (confirmed != true) return;
    try {
      final n = await ref.read(apiProvider).clearHistory();
      ref.invalidate(historyProvider);
      ref.read(latestEventProvider.notifier).set(null);
      if (context.mounted) showSnack(context, 'Cleared $n visits');
    } on ApiException catch (e) {
      if (context.mounted) showSnack(context, e.message, error: true);
    }
  }

  void _open(BuildContext context, VisitorEvent e, String tag) {
    Navigator.of(context).push(MaterialPageRoute(
      builder: (_) => EventDetailScreen(event: e, heroTag: tag),
    ));
  }

  List<VisitorEvent> _applyFilter(List<VisitorEvent> events) {
    switch (_filter) {
      case 'known':
        return events.where((e) => e.knownCount > 0).toList();
      case 'unknown':
        return events
            .where((e) => e.totalPeople > 0 && e.knownCount == 0)
            .toList();
      case 'multiple':
        return events.where((e) => e.totalPeople > 1).toList();
      default:
        return events;
    }
  }

  @override
  Widget build(BuildContext context) {
    final history = ref.watch(historyProvider);
    final query = ref.watch(historySearchProvider);
    final wsState = ref.watch(wsStateProvider).value;
    final reduce = context.reduceMotion;
    final text = Theme.of(context).textTheme;

    return Scaffold(
      backgroundColor: Colors.transparent,
      body: MeshScaffoldBody(
        child: SafeArea(
          bottom: false,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              // ── Header ──────────────────────────────────────
              Padding(
                padding: const EdgeInsets.fromLTRB(T.s16, T.s12, T.s16, 0),
                child: _buildHeader(wsState),
              ),

              const SizedBox(height: T.s20),

              // ── Title ───────────────────────────────────────
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: T.s16),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text('Door History',
                              style: text.headlineMedium?.copyWith(
                                fontWeight: FontWeight.w800,
                                color: T.textPrimary,
                              )),
                          const SizedBox(height: T.s4),
                          Text('Recent activity at your door',
                              style: text.bodyMedium?.copyWith(
                                color: T.textTertiary,
                              )),
                        ],
                      ),
                    ),
                    // Date selector
                    Container(
                      padding: const EdgeInsets.symmetric(
                          horizontal: T.s12, vertical: T.s8),
                      decoration: BoxDecoration(
                        color: T.bgCard,
                        borderRadius: BorderRadius.circular(T.rMd),
                        border: Border.all(color: T.border),
                      ),
                      child: Row(
                        children: [
                          Icon(Icons.calendar_today,
                              size: 16, color: T.textTertiary),
                          const SizedBox(width: T.s6),
                          Text('Today',
                              style: text.labelMedium?.copyWith(
                                color: T.textPrimary,
                                fontWeight: FontWeight.w600,
                              )),
                          const SizedBox(width: T.s4),
                          Icon(Icons.keyboard_arrow_down,
                              size: 18, color: T.textTertiary),
                        ],
                      ),
                    ),
                  ],
                ),
              ),

              const SizedBox(height: T.s16),

              // ── Search field ────────────────────────────────
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: T.s16),
                child: ContentWidth(
                  child: TextField(
                    onChanged: (v) =>
                        ref.read(historySearchProvider.notifier).set(v),
                    decoration: InputDecoration(
                      hintText: 'Search events, people, or keywords...',
                      hintStyle: TextStyle(
                          color: T.textTertiary, fontSize: 14),
                      prefixIcon:
                          Icon(Icons.search, color: T.textTertiary),
                      suffixIcon: query.isEmpty
                          ? null
                          : IconButton(
                              icon: Icon(Icons.clear,
                                  color: T.textTertiary),
                              tooltip: 'Clear search',
                              onPressed: () => ref
                                  .read(historySearchProvider.notifier)
                                  .set(''),
                            ),
                      filled: true,
                      fillColor: T.bgCard,
                      border: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(T.rMd),
                        borderSide: BorderSide(color: T.border),
                      ),
                      enabledBorder: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(T.rMd),
                        borderSide: BorderSide(color: T.border),
                      ),
                      contentPadding: const EdgeInsets.symmetric(
                          horizontal: T.s16, vertical: T.s12),
                    ),
                    textInputAction: TextInputAction.search,
                  ),
                ),
              ),

              const SizedBox(height: T.s12),

              // ── Filter pills ────────────────────────────────
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: T.s16),
                child: SingleChildScrollView(
                  scrollDirection: Axis.horizontal,
                  child: Row(
                    children: [
                      _FilterPill(
                        label: 'All',
                        icon: Icons.grid_view,
                        selected: _filter == 'all',
                        color: T.primary,
                        onTap: () => setState(() => _filter = 'all'),
                      ),
                      const SizedBox(width: T.s8),
                      _FilterPill(
                        label: 'Known',
                        icon: Icons.check_circle,
                        selected: _filter == 'known',
                        color: T.known,
                        onTap: () =>
                            setState(() => _filter = 'known'),
                      ),
                      const SizedBox(width: T.s8),
                      _FilterPill(
                        label: 'Unknown',
                        icon: Icons.warning_amber_rounded,
                        selected: _filter == 'unknown',
                        color: T.unknown,
                        onTap: () =>
                            setState(() => _filter = 'unknown'),
                      ),
                      const SizedBox(width: T.s8),
                      _FilterPill(
                        label: 'Multiple people',
                        icon: Icons.groups,
                        selected: _filter == 'multiple',
                        color: T.multi,
                        onTap: () =>
                            setState(() => _filter = 'multiple'),
                      ),
                    ],
                  ),
                ),
              ),

              const SizedBox(height: T.s16),

              // ── Event list ──────────────────────────────────
              Expanded(
                child: RefreshIndicator(
                  onRefresh: () async {
                    ref.invalidate(historyProvider);
                    await ref.read(historyProvider.future);
                  },
                  child: switch (history) {
                    AsyncData(:final value) when value.isEmpty =>
                      query.isEmpty
                          ? _empty(context)
                          : _noMatches(context, query),
                    AsyncData(:final value) => _buildTimeline(
                        context, _applyFilter(value), reduce),
                    AsyncError(:final error) =>
                      _error(context, ref, '$error'),
                    _ => const Center(child: CircularProgressIndicator()),
                  },
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildHeader(WsState? wsState) {
    final text = Theme.of(context).textTheme;
    return Row(
      crossAxisAlignment: CrossAxisAlignment.center,
      children: [
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
        Container(
          width: 44,
          height: 44,
          decoration: BoxDecoration(
            color: T.primary,
            shape: BoxShape.circle,
          ),
          child: const Icon(Icons.mic, color: Colors.white, size: 22),
        ),
        const SizedBox(width: T.s8),
        _connPill(wsState),
      ],
    );
  }

  Widget _connPill(WsState? s) {
    return switch (s) {
      WsState.connected => StatusPill(
          label: 'Live',
          color: T.success,
          icon: Icons.wifi),
      WsState.connecting => const StatusPill(
          label: 'Connecting', color: T.accent, icon: Icons.wifi_find),
      _ => StatusPill(
          label: 'Offline • Local AI',
          color: T.danger,
          icon: Icons.wifi_off),
    };
  }

  /// Build timeline with Today / Yesterday sections
  Widget _buildTimeline(
      BuildContext context, List<VisitorEvent> events, bool reduce) {
    if (events.isEmpty) {
      return _empty(context);
    }

    // Group events by day
    final now = DateTime.now();
    final today = <VisitorEvent>[];
    final yesterday = <VisitorEvent>[];
    final older = <VisitorEvent>[];

    for (final e in events) {
      final dt = DateTime.tryParse(e.timestamp);
      if (dt == null) {
        older.add(e);
        continue;
      }
      final local = dt.toLocal();
      if (local.year == now.year &&
          local.month == now.month &&
          local.day == now.day) {
        today.add(e);
      } else {
        final yday = now.subtract(const Duration(days: 1));
        if (local.year == yday.year &&
            local.month == yday.month &&
            local.day == yday.day) {
          yesterday.add(e);
        } else {
          older.add(e);
        }
      }
    }

    return ContentWidth(
      child: ListView(
        padding: const EdgeInsets.fromLTRB(T.s16, 0, T.s16, 120),
        children: [
          if (today.isNotEmpty) ...[
            _SectionHeader('Today'),
            const SizedBox(height: T.s12),
            for (int i = 0; i < today.length; i++) ...[
              _buildEventRow(context, today[i], i, reduce),
              if (i < today.length - 1) const SizedBox(height: T.s12),
            ],
          ],
          if (yesterday.isNotEmpty) ...[
            const SizedBox(height: T.s24),
            _SectionHeader('Yesterday'),
            const SizedBox(height: T.s12),
            for (int i = 0; i < yesterday.length; i++) ...[
              _buildEventRow(context, yesterday[i], today.length + i, reduce),
              if (i < yesterday.length - 1) const SizedBox(height: T.s12),
            ],
          ],
          if (older.isNotEmpty) ...[
            const SizedBox(height: T.s24),
            _SectionHeader('Earlier'),
            const SizedBox(height: T.s12),
            for (int i = 0; i < older.length; i++) ...[
              _buildEventRow(context, older[i],
                  today.length + yesterday.length + i, reduce),
              if (i < older.length - 1) const SizedBox(height: T.s12),
            ],
          ],
        ],
      ),
    );
  }

  Widget _buildEventRow(
      BuildContext context, VisitorEvent e, int index, bool reduce) {
    final text = Theme.of(context).textTheme;
    final api = ref.watch(apiProvider);
    final tag = 'event-${e.eventId.isNotEmpty ? e.eventId : index}';

    // Determine status
    final isKnown = e.knownCount > 0;
    final isMultiple = e.totalPeople > 1;
    final statusColor = isKnown
        ? T.known
        : isMultiple
            ? T.multi
            : e.totalPeople > 0
                ? T.unknown
                : T.textTertiary;
    final statusBg = isKnown
        ? T.knownBg
        : isMultiple
            ? T.multiBg
            : e.totalPeople > 0
                ? T.unknownBg
                : T.bgSubtle;
    final statusIcon = isMultiple
        ? Icons.groups
        : isKnown
            ? Icons.person
            : e.totalPeople > 0
                ? Icons.warning_amber_rounded
                : Icons.sensors_off;

    // Status text
    final statusText = isMultiple
        ? '${e.totalPeople} people • ${e.knownCount} known'
        : isKnown
            ? '1 person • Known'
            : e.totalPeople > 0
                ? '1 person • Unknown'
                : 'No person detected';

    // Title text
    final titleText = isMultiple
        ? '${isKnown ? e.name : "Unknown"} + ${e.totalPeople - 1} ${e.totalPeople - 1 == 1 ? "other" : "others"}'
        : isKnown
            ? e.name
            : e.totalPeople > 0
                ? 'Unknown visitor'
                : 'No activity';

    // Description
    final desc = e.sceneSummary.isNotEmpty
        ? e.sceneSummary
        : e.totalPeople > 0
            ? 'Person detected at the door.'
            : 'The area in front of the door was clear.';

    Widget row = GestureDetector(
      onTap: () => _open(context, e, tag),
      child: Container(
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
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // Camera thumbnail
            ClipRRect(
              borderRadius: BorderRadius.circular(T.rSm),
              child: SizedBox(
                width: 100,
                height: 80,
                child: Stack(
                  fit: StackFit.expand,
                  children: [
                    e.eventId.isNotEmpty
                        ? Image.network(
                            api.snapshotUrl(e.eventId),
                            fit: BoxFit.cover,
                            gaplessPlayback: true,
                            errorBuilder: (_, _, _) =>
                                Container(color: T.bgSubtle),
                            loadingBuilder: (_, child, progress) =>
                                progress == null
                                    ? child
                                    : Container(color: T.bgSubtle),
                          )
                        : Container(color: T.bgSubtle),
                    // Time badge on thumbnail
                    Positioned(
                      left: T.s4,
                      top: T.s4,
                      child: Container(
                        padding: const EdgeInsets.symmetric(
                            horizontal: T.s4, vertical: 2),
                        decoration: BoxDecoration(
                          color: Colors.black.withValues(alpha: 0.6),
                          borderRadius: BorderRadius.circular(4),
                        ),
                        child: Text(
                          _shortTime(e.timestamp),
                          style: const TextStyle(
                            color: Colors.white,
                            fontSize: 10,
                            fontWeight: FontWeight.w600,
                          ),
                        ),
                      ),
                    ),
                    // Duration badge
                    Positioned(
                      right: T.s4,
                      bottom: T.s4,
                      child: Container(
                        padding: const EdgeInsets.symmetric(
                            horizontal: T.s4, vertical: 2),
                        decoration: BoxDecoration(
                          color: Colors.black.withValues(alpha: 0.6),
                          borderRadius: BorderRadius.circular(4),
                        ),
                        child: Row(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            const Icon(Icons.videocam,
                                size: 10, color: Colors.white),
                            const SizedBox(width: 2),
                            Text('00:${(index % 20 + 5).toString().padLeft(2, '0')}',
                                style: const TextStyle(
                                  color: Colors.white,
                                  fontSize: 9,
                                  fontWeight: FontWeight.w500,
                                )),
                          ],
                        ),
                      ),
                    ),
                  ],
                ),
              ),
            ),

            const SizedBox(width: T.s12),

            // Event details
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  // Status badge
                  Container(
                    padding: const EdgeInsets.symmetric(
                        horizontal: T.s8, vertical: T.s2),
                    decoration: BoxDecoration(
                      color: statusBg,
                      borderRadius: BorderRadius.circular(T.rPill),
                    ),
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Icon(statusIcon, size: 12, color: statusColor),
                        const SizedBox(width: T.s4),
                        Text(statusText,
                            style: text.labelSmall?.copyWith(
                              color: statusColor,
                              fontWeight: FontWeight.w600,
                              fontSize: 11,
                            )),
                      ],
                    ),
                  ),
                  const SizedBox(height: T.s6),
                  // Title
                  Text(titleText,
                      style: text.titleMedium?.copyWith(
                        fontWeight: FontWeight.w700,
                        color: T.textPrimary,
                        fontSize: 15,
                      ),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis),
                  const SizedBox(height: T.s4),
                  // Description
                  Text(
                    desc,
                    style: text.bodySmall?.copyWith(
                      color: T.textTertiary,
                      height: 1.3,
                      fontSize: 12,
                    ),
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                  ),
                ],
              ),
            ),

            // Arrow
            Padding(
              padding: const EdgeInsets.only(top: T.s20),
              child: Icon(Icons.chevron_right,
                  size: 22, color: T.textTertiary),
            ),
          ],
        ),
      ),
    );

    if (reduce) return row;
    return row
        .animate()
        .fadeIn(
            duration: T.med,
            delay: Duration(milliseconds: 40 * (index.clamp(0, 8))))
        .slideY(begin: 0.08, end: 0, curve: T.easeExpo);
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

  Widget _noMatches(BuildContext context, String query) {
    return ListView(
      padding: const EdgeInsets.fromLTRB(T.s32, T.s32, T.s32, 120),
      children: [
        const SizedBox(height: 48),
        Center(
          child: Text(
            'No visits match "$query".',
            style: Theme.of(context)
                .textTheme
                .bodyLarge
                ?.copyWith(color: T.textSecondary),
            textAlign: TextAlign.center,
          ),
        ),
      ],
    );
  }

  Widget _empty(BuildContext context) {
    return ListView(
      padding: const EdgeInsets.fromLTRB(T.s32, T.s32, T.s32, 120),
      children: [
        const SizedBox(height: 64),
        Entrance(
          child: Container(
            padding: const EdgeInsets.all(T.s32),
            decoration: BoxDecoration(
              color: T.bgCard,
              borderRadius: BorderRadius.circular(T.rLg),
              border: Border.all(color: T.border),
            ),
            child: Column(
              children: [
                Container(
                  width: 88,
                  height: 88,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    color: T.primaryLighter,
                  ),
                  child: Icon(Icons.inbox_outlined,
                      size: 40, color: T.textTertiary),
                ),
                const SizedBox(height: T.s16),
                Text('No visits recorded yet',
                    style: Theme.of(context)
                        .textTheme
                        .titleMedium
                        ?.copyWith(color: T.textPrimary)),
                const SizedBox(height: T.s8),
                Text('Pull down to refresh.',
                    style: Theme.of(context)
                        .textTheme
                        .bodyMedium
                        ?.copyWith(color: T.textTertiary)),
              ],
            ),
          ),
        ),
      ],
    );
  }

  Widget _error(BuildContext context, WidgetRef ref, String message) {
    return ListView(
      padding: const EdgeInsets.fromLTRB(T.s32, T.s32, T.s32, 120),
      children: [
        const SizedBox(height: 64),
        Entrance(
          child: Container(
            padding: const EdgeInsets.all(T.s32),
            decoration: BoxDecoration(
              color: T.bgCard,
              borderRadius: BorderRadius.circular(T.rLg),
              border: Border.all(color: T.dangerBorder),
            ),
            child: Column(
              children: [
                Icon(Icons.cloud_off, size: 56, color: T.danger),
                const SizedBox(height: T.s16),
                Text(message,
                    textAlign: TextAlign.center,
                    style: TextStyle(color: T.textSecondary)),
                const SizedBox(height: T.s16),
                SizedBox(
                  height: T.minTouch,
                  child: FilledButton.icon(
                    onPressed: () => ref.invalidate(historyProvider),
                    icon: const Icon(Icons.refresh),
                    label: const Text('Retry'),
                  ),
                ),
              ],
            ),
          ),
        ),
      ],
    );
  }
}

/// Section header (Today / Yesterday / Earlier)
class _SectionHeader extends StatelessWidget {
  const _SectionHeader(this.title);
  final String title;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(top: T.s4),
      child: Text(title,
          style: Theme.of(context).textTheme.titleMedium?.copyWith(
                fontWeight: FontWeight.w700,
                color: T.textPrimary,
                fontSize: 16,
              )),
    );
  }
}

/// Filter pill for the history screen
class _FilterPill extends StatelessWidget {
  const _FilterPill({
    required this.label,
    required this.icon,
    required this.selected,
    required this.color,
    required this.onTap,
  });

  final String label;
  final IconData icon;
  final bool selected;
  final Color color;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: AnimatedContainer(
        duration: T.fast,
        padding:
            const EdgeInsets.symmetric(horizontal: T.s12, vertical: T.s8),
        decoration: BoxDecoration(
          color: selected ? color.withValues(alpha: 0.1) : T.bgCard,
          borderRadius: BorderRadius.circular(T.rPill),
          border: Border.all(
            color: selected ? color.withValues(alpha: 0.3) : T.border,
          ),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon,
                size: 15,
                color: selected ? color : T.textTertiary),
            const SizedBox(width: T.s6),
            Text(label,
                style: Theme.of(context).textTheme.labelMedium?.copyWith(
                      color: selected ? color : T.textSecondary,
                      fontWeight:
                          selected ? FontWeight.w600 : FontWeight.w500,
                    )),
          ],
        ),
      ),
    );
  }
}
