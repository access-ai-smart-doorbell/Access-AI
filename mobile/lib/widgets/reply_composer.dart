import 'package:flutter/material.dart';

import '../core/tokens.dart';

/// A compose row for speaking a reply at the door: quick-reply chips for the
/// common phrases plus a free-text field. Calls [onSend] with the text; the
/// parent performs POST /reply and shows the result.
class ReplyComposer extends StatefulWidget {
  const ReplyComposer({
    super.key,
    required this.onSend,
    this.sending = false,
    this.quickReplies = defaultQuickReplies,
  });

  final Future<void> Function(String text) onSend;
  final bool sending;

  /// The chips to show. Supplied by the caller from the server (Phase 17); the
  /// constant is the offline / older-server fallback.
  final List<String> quickReplies;

  static const defaultQuickReplies = <String>[
    "I'll be right there",
    'Please leave it at the door',
    "Sorry, I'm not available",
    'Who is it?',
  ];

  @override
  State<ReplyComposer> createState() => _ReplyComposerState();
}

class _ReplyComposerState extends State<ReplyComposer> {
  final _controller = TextEditingController();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  Future<void> _send(String text) async {
    final t = text.trim();
    if (t.isEmpty || widget.sending) return;
    await widget.onSend(t);
    if (mounted) _controller.clear();
  }

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text('SPEAK A REPLY',
            style: text.labelSmall?.copyWith(
                color: T.muted,
                fontWeight: FontWeight.w700,
                letterSpacing: 1.4)),
        const SizedBox(height: T.s8),
        Wrap(
          spacing: T.s8,
          runSpacing: T.s8,
          children: [
            for (final q in widget.quickReplies)
              ActionChip(
                avatar: const Icon(Icons.bolt,
                    size: 16, color: T.seed),
                label: Text(q),
                onPressed: widget.sending ? null : () => _send(q),
                backgroundColor: Colors.white.withValues(alpha: 0.06),
                side: BorderSide(
                    color: Colors.white.withValues(alpha: 0.14)),
                shape: const StadiumBorder(),
                // Comfortable tap target for the chips.
                padding: const EdgeInsets.symmetric(
                    horizontal: T.s12, vertical: T.s8),
              ),
          ],
        ),
        const SizedBox(height: T.s12),
        Row(
          children: [
            Expanded(
              child: TextField(
                controller: _controller,
                textInputAction: TextInputAction.send,
                onSubmitted: _send,
                minLines: 1,
                maxLines: 3,
                decoration: const InputDecoration(
                  hintText: 'Type a reply to speak…',
                ),
              ),
            ),
            const SizedBox(width: T.s8),
            SizedBox(
              width: T.minTouch,
              height: T.minTouch,
              child: IconButton.filled(
                onPressed:
                    widget.sending ? null : () => _send(_controller.text),
                tooltip: 'Speak this reply',
                icon: widget.sending
                    ? const SizedBox(
                        width: 20,
                        height: 20,
                        child: CircularProgressIndicator(strokeWidth: 2))
                    : const Icon(Icons.send),
              ),
            ),
          ],
        ),
      ],
    );
  }
}
