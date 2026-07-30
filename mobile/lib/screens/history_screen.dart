import 'package:flutter/material.dart';
import 'package:flutter_animate/flutter_animate.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../core/glass.dart';
import '../core/motion.dart';
import '../core/tokens.dart';
import '../core/ui.dart';
import '../models/visitor_event.dart';
import '../services/api_service.dart';
import '../state/providers.dart';
import '../widgets/event_card.dart';
import 'event_detail_screen.dart';

/// History — every visit, newest first. Pull to refresh; tap a card for a
/// shared-element hero transition into the detail view; clear all with a
/// confirmation. Cards stagger in (disabled under reduce-motion).
class HistoryScreen extends ConsumerWidget {
  const HistoryScreen({super.key});

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

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final history = ref.watch(historyProvider);
    final query = ref.watch(historySearchProvider);
    final reduce = context.reduceMotion;

    return Scaffold(
      backgroundColor: Colors.transparent,
      appBar: AppBar(
        title: const Text('History'),
        actions: [
          IconButton(
            onPressed: () => _clear(context, ref),
            icon: const Icon(Icons.delete_sweep_outlined),
            tooltip: 'Clear all history',
          ),
        ],
      ),
      body: MeshScaffoldBody(
        child: SafeArea(
          bottom: false,
          child: Column(
            children: [
              // Server-side search (?q=): name, intent, words said, or a date
              // prefix like 2026-07-19. Objective 7: searchable history.
              Padding(
                padding: const EdgeInsets.fromLTRB(T.s16, T.s12, T.s16, 0),
                child: ContentWidth(
                  child: TextField(
                    onChanged: (v) =>
                        ref.read(historySearchProvider.notifier).set(v),
                    decoration: InputDecoration(
                      hintText: 'Search name, intent, or date…',
                      prefixIcon: const Icon(Icons.search),
                      suffixIcon: query.isEmpty
                          ? null
                          : IconButton(
                              icon: const Icon(Icons.clear),
                              tooltip: 'Clear search',
                              onPressed: () => ref
                                  .read(historySearchProvider.notifier)
                                  .set(''),
                            ),
                    ),
                    textInputAction: TextInputAction.search,
                  ),
                ),
              ),
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
                    AsyncData(:final value) => ContentWidth(
                        child: ListView.separated(
                          // 120 bottom clears the floating glass nav pill.
                          padding: const EdgeInsets.fromLTRB(
                              T.s16, T.s16, T.s16, 120),
                          itemCount: value.length,
                          separatorBuilder: (_, _) =>
                              const SizedBox(height: T.s12),
                          itemBuilder: (context, i) {
                            final e = value[i];
                            final tag =
                                'event-${e.eventId.isNotEmpty ? e.eventId : i}';
                            final card = EventCard(
                              event: e,
                              heroTag: tag,
                              onTap: () => _open(context, e, tag),
                            );
                            if (reduce) return card;
                            return card
                                .animate()
                                .fadeIn(
                                    duration: T.med,
                                    delay: Duration(
                                        milliseconds: 40 * (i.clamp(0, 8))))
                                .slideY(
                                    begin: 0.08, end: 0, curve: T.easeExpo);
                          },
                        ),
                      ),
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

  Widget _noMatches(BuildContext context, String query) {
    // Scrollable so pull-to-refresh keeps working with no results.
    return ListView(
      padding: const EdgeInsets.fromLTRB(T.s32, T.s32, T.s32, 120),
      children: [
        const SizedBox(height: 48),
        Center(
          child: Text(
            'No visits match “$query”.',
            style: Theme.of(context).textTheme.bodyLarge,
            textAlign: TextAlign.center,
          ),
        ),
      ],
    );
  }

  Widget _empty(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    // Wrapped in a scroll view so pull-to-refresh works even when empty.
    return ListView(
      padding: const EdgeInsets.fromLTRB(T.s32, T.s32, T.s32, 120),
      children: [
        const SizedBox(height: 64),
        Entrance(
          child: GlassCard(
            padding: const EdgeInsets.all(T.s32),
            child: Column(
              children: [
                Container(
                  width: 88,
                  height: 88,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    gradient: LinearGradient(
                      begin: Alignment.topLeft,
                      end: Alignment.bottomRight,
                      colors: [
                        T.jarvis1.withValues(alpha: 0.20),
                        T.jarvis3.withValues(alpha: 0.20),
                      ],
                    ),
                  ),
                  child: Icon(Icons.inbox_outlined,
                      size: 40, color: cs.onSurface.withValues(alpha: 0.7)),
                ),
                const SizedBox(height: T.s16),
                Text('No visits recorded yet',
                    style: Theme.of(context).textTheme.titleMedium),
                const SizedBox(height: T.s8),
                Text('Pull down to refresh.',
                    style: Theme.of(context)
                        .textTheme
                        .bodyMedium
                        ?.copyWith(color: cs.onSurfaceVariant)),
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
          child: GlassCard(
            borderTint: T.danger,
            padding: const EdgeInsets.all(T.s32),
            child: Column(
              children: [
                const Icon(Icons.cloud_off, size: 56),
                const SizedBox(height: T.s16),
                Text(message, textAlign: TextAlign.center),
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
