import 'package:flutter/material.dart';
import 'package:flutter_animate/flutter_animate.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:video_player/video_player.dart';

import '../core/motion.dart';
import '../core/tokens.dart';
import '../core/ui.dart';
import '../services/api_service.dart';
import '../services/events_service.dart';
import '../state/providers.dart';
import '../widgets/status_pill.dart';

/// Clips — "Recorded Videos" screen.
/// Lists all saved event video clips from the server with metadata (date,
/// duration, file size) and lets the user tap to play in a full-screen modal.
class ClipsScreen extends ConsumerStatefulWidget {
  const ClipsScreen({super.key});

  @override
  ConsumerState<ClipsScreen> createState() => _ClipsScreenState();
}

class _ClipsScreenState extends ConsumerState<ClipsScreen> {
  @override
  Widget build(BuildContext context) {
    final clips = ref.watch(clipsProvider);
    final wsState = ref.watch(wsStateProvider).value;
    final text = Theme.of(context).textTheme;
    final reduce = context.reduceMotion;

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
                child: _buildHeader(wsState, text),
              ),

              const SizedBox(height: T.s20),

              // ── Title ───────────────────────────────────────
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: T.s16),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('Recorded Videos',
                        style: text.headlineMedium?.copyWith(
                          fontWeight: FontWeight.w800,
                          color: T.textPrimary,
                        )),
                    const SizedBox(height: T.s4),
                    Text('Doorbell event recordings',
                        style: text.bodyMedium?.copyWith(
                          color: T.textTertiary,
                        )),
                  ],
                ),
              ),

              const SizedBox(height: T.s16),

              // ── Clip list ──────────────────────────────────
              Expanded(
                child: RefreshIndicator(
                  onRefresh: () async {
                    ref.invalidate(clipsProvider);
                    await ref.read(clipsProvider.future);
                  },
                  child: switch (clips) {
                    AsyncData(:final value) when value.isEmpty =>
                      _empty(context),
                    AsyncData(:final value) =>
                      _buildClipList(context, value, reduce),
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

  Widget _buildHeader(WsState? wsState, TextTheme text) {
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

  Widget _buildClipList(
      BuildContext context, List<Map<String, dynamic>> clips, bool reduce) {
    return ContentWidth(
      child: ListView.separated(
        padding: const EdgeInsets.fromLTRB(T.s16, 0, T.s16, 120),
        itemCount: clips.length,
        separatorBuilder: (_, i) => const SizedBox(height: T.s12),
        itemBuilder: (context, index) {
          final clip = clips[index];
          Widget row = _ClipRow(
            clip: clip,
            index: index,
            onPlay: () => _openPlayer(context, clip),
            onDelete: () => _deleteClip(context, clip),
          );
          if (reduce) return row;
          return row
              .animate()
              .fadeIn(
                  duration: T.med,
                  delay: Duration(milliseconds: 40 * (index.clamp(0, 8))))
              .slideY(begin: 0.08, end: 0, curve: T.easeExpo);
        },
      ),
    );
  }

  void _openPlayer(BuildContext context, Map<String, dynamic> clip) {
    final api = ref.read(apiProvider);
    final filename = clip['filename'] as String? ?? '';
    if (filename.isEmpty) return;

    final url = api.clipUrl(filename);
    Navigator.of(context).push(MaterialPageRoute(
      builder: (_) => _VideoPlayerScreen(
        url: url,
        title: filename,
        clip: clip,
      ),
    ));
  }

  Future<void> _deleteClip(
      BuildContext context, Map<String, dynamic> clip) async {
    final filename = clip['filename'] as String? ?? '';
    if (filename.isEmpty) return;

    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Delete this recording?'),
        content: const Text(
            'This permanently removes the video clip. It cannot be undone.'),
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
    if (confirmed != true || !mounted) return;

    try {
      await ref.read(apiProvider).deleteClip(filename);
      ref.invalidate(clipsProvider);
      if (mounted) showSnack(context, 'Recording deleted');
    } on ApiException catch (e) {
      if (mounted) showSnack(context, e.message, error: true);
    }
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
                  child: Icon(Icons.videocam_off_outlined,
                      size: 40, color: T.textTertiary),
                ),
                const SizedBox(height: T.s16),
                Text('No recorded videos',
                    style: Theme.of(context)
                        .textTheme
                        .titleMedium
                        ?.copyWith(color: T.textPrimary)),
                const SizedBox(height: T.s8),
                Text(
                    'Event recordings will appear here when the doorbell captures visitors.',
                    textAlign: TextAlign.center,
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
                    onPressed: () => ref.invalidate(clipsProvider),
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

// ─── Clip row widget ─────────────────────────────────────────────────────────

class _ClipRow extends StatelessWidget {
  const _ClipRow({
    required this.clip,
    required this.index,
    required this.onPlay,
    required this.onDelete,
  });

  final Map<String, dynamic> clip;
  final int index;
  final VoidCallback onPlay;
  final VoidCallback onDelete;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final filename = clip['filename'] as String? ?? 'unknown.mp4';
    final sizeBytes = (clip['size_bytes'] as num?)?.toInt() ?? 0;
    final modified = (clip['modified'] as num?)?.toDouble() ?? 0;
    final eventId = clip['event_id'] as String? ?? '';
    final duration = clip['duration_seconds'] as num?;

    // Format file size
    final sizeMb = sizeBytes / (1024 * 1024);
    final sizeStr = sizeMb >= 1
        ? '${sizeMb.toStringAsFixed(1)} MB'
        : '${(sizeBytes / 1024).toStringAsFixed(0)} KB';

    // Format date
    final dt = modified > 0
        ? DateTime.fromMillisecondsSinceEpoch((modified * 1000).toInt())
            .toLocal()
        : null;
    final dateStr = dt != null ? _formatDate(dt) : '';
    final timeStr = dt != null ? _formatTime(dt) : '';

    // Format duration
    final durationStr = duration != null
        ? _formatDuration(duration.toInt())
        : '--:--';

    return GestureDetector(
      onTap: onPlay,
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
          crossAxisAlignment: CrossAxisAlignment.center,
          children: [
            // Video icon / thumbnail placeholder
            Container(
              width: 72,
              height: 56,
              decoration: BoxDecoration(
                gradient: LinearGradient(
                  begin: Alignment.topLeft,
                  end: Alignment.bottomRight,
                  colors: [
                    T.primary.withValues(alpha: 0.15),
                    T.accent.withValues(alpha: 0.10),
                  ],
                ),
                borderRadius: BorderRadius.circular(T.rSm),
                border: Border.all(
                    color: T.primary.withValues(alpha: 0.2)),
              ),
              child: Stack(
                alignment: Alignment.center,
                children: [
                  // Play icon
                  Container(
                    width: 32,
                    height: 32,
                    decoration: BoxDecoration(
                      color: T.primary.withValues(alpha: 0.9),
                      shape: BoxShape.circle,
                    ),
                    child: const Icon(Icons.play_arrow,
                        size: 20, color: Colors.white),
                  ),
                  // Duration badge
                  Positioned(
                    right: T.s4,
                    bottom: T.s4,
                    child: Container(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 4, vertical: 1),
                      decoration: BoxDecoration(
                        color: Colors.black.withValues(alpha: 0.6),
                        borderRadius: BorderRadius.circular(3),
                      ),
                      child: Text(durationStr,
                          style: const TextStyle(
                            color: Colors.white,
                            fontSize: 9,
                            fontWeight: FontWeight.w600,
                          )),
                    ),
                  ),
                ],
              ),
            ),

            const SizedBox(width: T.s12),

            // Details
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  // Event label
                  Row(
                    children: [
                      Icon(Icons.videocam, size: 14, color: T.primary),
                      const SizedBox(width: T.s4),
                      Expanded(
                        child: Text(
                          eventId.isNotEmpty
                              ? 'Event $eventId'
                              : filename.replaceAll('.mp4', ''),
                          style: text.titleSmall?.copyWith(
                            fontWeight: FontWeight.w700,
                            color: T.textPrimary,
                            fontSize: 14,
                          ),
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: T.s4),
                  // Date & size
                  Row(
                    children: [
                      if (dateStr.isNotEmpty) ...[
                        Icon(Icons.calendar_today,
                            size: 11, color: T.textTertiary),
                        const SizedBox(width: 3),
                        Text(dateStr,
                            style: text.bodySmall?.copyWith(
                              color: T.textTertiary,
                              fontSize: 11,
                            )),
                        const SizedBox(width: T.s8),
                      ],
                      if (timeStr.isNotEmpty) ...[
                        Icon(Icons.access_time,
                            size: 11, color: T.textTertiary),
                        const SizedBox(width: 3),
                        Text(timeStr,
                            style: text.bodySmall?.copyWith(
                              color: T.textTertiary,
                              fontSize: 11,
                            )),
                        const SizedBox(width: T.s8),
                      ],
                      Icon(Icons.storage,
                          size: 11, color: T.textTertiary),
                      const SizedBox(width: 3),
                      Text(sizeStr,
                          style: text.bodySmall?.copyWith(
                            color: T.textTertiary,
                            fontSize: 11,
                          )),
                    ],
                  ),
                ],
              ),
            ),

            // Delete button
            GestureDetector(
              onTap: onDelete,
              child: Container(
                width: 36,
                height: 36,
                decoration: BoxDecoration(
                  color: T.dangerBg,
                  shape: BoxShape.circle,
                ),
                child: Icon(Icons.delete_outline,
                    size: 18, color: T.danger),
              ),
            ),
          ],
        ),
      ),
    );
  }

  String _formatDate(DateTime dt) {
    final now = DateTime.now();
    if (dt.year == now.year && dt.month == now.month && dt.day == now.day) {
      return 'Today';
    }
    final yday = now.subtract(const Duration(days: 1));
    if (dt.year == yday.year &&
        dt.month == yday.month &&
        dt.day == yday.day) {
      return 'Yesterday';
    }
    const months = [
      '', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
      'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'
    ];
    return '${months[dt.month]} ${dt.day}';
  }

  String _formatTime(DateTime dt) {
    final hh = dt.hour % 12 == 0 ? 12 : dt.hour % 12;
    final mm = dt.minute.toString().padLeft(2, '0');
    final ap = dt.hour < 12 ? 'AM' : 'PM';
    return '$hh:$mm $ap';
  }

  String _formatDuration(int seconds) {
    final m = seconds ~/ 60;
    final s = seconds % 60;
    return '${m.toString().padLeft(2, '0')}:${s.toString().padLeft(2, '0')}';
  }
}

// ─── Full-screen video player ────────────────────────────────────────────────

class _VideoPlayerScreen extends StatefulWidget {
  const _VideoPlayerScreen({
    required this.url,
    required this.title,
    required this.clip,
  });

  final String url;
  final String title;
  final Map<String, dynamic> clip;

  @override
  State<_VideoPlayerScreen> createState() => _VideoPlayerScreenState();
}

class _VideoPlayerScreenState extends State<_VideoPlayerScreen> {
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
    final text = Theme.of(context).textTheme;
    final eventId = widget.clip['event_id'] as String? ?? '';
    final displayTitle = eventId.isNotEmpty
        ? 'Event $eventId'
        : widget.title.replaceAll('.mp4', '');

    return Scaffold(
      backgroundColor: Colors.black,
      appBar: AppBar(
        backgroundColor: Colors.black,
        foregroundColor: Colors.white,
        title: Text(displayTitle,
            style: text.titleMedium?.copyWith(
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
            ? _buildError()
            : !_initialized
                ? _buildLoading()
                : _buildPlayer(),
      ),
    );
  }

  Widget _buildLoading() {
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        const CircularProgressIndicator(color: Colors.white),
        const SizedBox(height: T.s16),
        Text('Loading video...',
            style: TextStyle(color: Colors.white.withValues(alpha: 0.7))),
      ],
    );
  }

  Widget _buildError() {
    return Padding(
      padding: const EdgeInsets.all(T.s32),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          const Icon(Icons.error_outline, size: 56, color: Colors.redAccent),
          const SizedBox(height: T.s16),
          Text(_error ?? 'Unknown error',
              textAlign: TextAlign.center,
              style: const TextStyle(color: Colors.white70)),
          const SizedBox(height: T.s16),
          FilledButton.icon(
            onPressed: () => Navigator.of(context).pop(),
            icon: const Icon(Icons.arrow_back),
            label: const Text('Go back'),
          ),
        ],
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
        // Video
        AspectRatio(
          aspectRatio: _controller.value.aspectRatio,
          child: VideoPlayer(_controller),
        ),

        const SizedBox(height: T.s16),

        // Progress bar
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: T.s24),
          child: Column(
            children: [
              // Slider
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
              // Time labels
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: T.s8),
                child: Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    Text(_formatDuration(position),
                        style: const TextStyle(
                            color: Colors.white70, fontSize: 12)),
                    Text(_formatDuration(duration),
                        style: const TextStyle(
                            color: Colors.white70, fontSize: 12)),
                  ],
                ),
              ),
            ],
          ),
        ),

        const SizedBox(height: T.s8),

        // Controls
        Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            // Rewind 10s
            IconButton(
              onPressed: () {
                final target =
                    position - const Duration(seconds: 10);
                _controller.seekTo(
                    target < Duration.zero ? Duration.zero : target);
              },
              icon: const Icon(Icons.replay_10),
              iconSize: 32,
              color: Colors.white,
            ),
            const SizedBox(width: T.s16),
            // Play/Pause
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
            // Forward 10s
            IconButton(
              onPressed: () {
                final target =
                    position + const Duration(seconds: 10);
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

  String _formatDuration(Duration d) {
    final m = d.inMinutes.remainder(60).toString().padLeft(2, '0');
    final s = d.inSeconds.remainder(60).toString().padLeft(2, '0');
    return '$m:$s';
  }
}
