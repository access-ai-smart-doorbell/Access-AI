import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:speech_to_text/speech_to_text.dart' as stt;

import '../core/glass.dart';
import '../core/motion.dart';
import '../core/parse.dart';
import '../core/tokens.dart';
import '../core/ui.dart';
import '../services/api_service.dart';
import '../services/audio_service.dart';
import '../state/providers.dart';

/// Voice Commands — "Ask AccessAI". Tap the mic, speak a question, and the
/// spoken/typed answer comes back from POST /command and is read aloud. Two
/// kinds of question work through the same path: fixed intents ("who's at the
/// door?", "any packages?") AND free-form VISUAL questions about the live scene
/// ("what colour is their dress", "what is he doing now") which the backend
/// routes to the VLM (ask_scene). A typed field is always available as a
/// fallback (device without speech recognition, or a user who prefers typing).
class VoiceScreen extends ConsumerStatefulWidget {
  const VoiceScreen({super.key});

  @override
  ConsumerState<VoiceScreen> createState() => _VoiceScreenState();
}

class _VoiceScreenState extends ConsumerState<VoiceScreen> {
  final stt.SpeechToText _speech = stt.SpeechToText();
  final _typed = TextEditingController();

  bool _speechReady = false;
  bool _listening = false;
  bool _busy = false;
  String _partial = '';
  String _answer = '';

  @override
  void initState() {
    super.initState();
    _initSpeech();
  }

  Future<void> _initSpeech() async {
    try {
      final ok = await _speech.initialize(
        onStatus: (s) {
          if (s == 'done' || s == 'notListening') {
            if (mounted) setState(() => _listening = false);
          }
        },
        onError: (_) {
          if (mounted) setState(() => _listening = false);
        },
      );
      if (mounted) setState(() => _speechReady = ok);
    } catch (_) {
      if (mounted) setState(() => _speechReady = false);
    }
  }

  @override
  void dispose() {
    _speech.stop();
    _typed.dispose();
    super.dispose();
  }

  Future<void> _toggleListen() async {
    if (_listening) {
      await _speech.stop();
      if (mounted) setState(() => _listening = false);
      if (_partial.trim().isNotEmpty) await _ask(_partial);
      return;
    }
    setState(() {
      _partial = '';
      _answer = '';
      _listening = true;
    });
    ref.read(audioProvider).sfx(Sfx.listen); // "I'm listening" earcon
    ref.read(audioProvider).announceOnly('Listening for your question');
    await _speech.listen(
      onResult: (r) {
        setState(() => _partial = r.recognizedWords);
        if (r.finalResult) {
          setState(() => _listening = false);
          if (r.recognizedWords.trim().isNotEmpty) _ask(r.recognizedWords);
        }
      },
      listenOptions: stt.SpeechListenOptions(
        listenFor: const Duration(seconds: 12),
        pauseFor: const Duration(seconds: 3),
        partialResults: true,
        cancelOnError: true,
      ),
    );
  }

  Future<void> _ask(String question) async {
    final q = question.trim();
    if (q.isEmpty || _busy) return;
    setState(() => _busy = true);
    try {
      final res = await ref.read(apiProvider).command(q);
      final answer = asStr(res['answer']).trim();
      setState(() => _answer = answer.isEmpty ? 'No answer available.' : answer);
      if (answer.isNotEmpty) {
        await ref.read(audioProvider).sfx(Sfx.success);
        await ref.read(audioProvider).speak(answer, ref.read(apiProvider));
      }
    } on ApiException catch (e) {
      setState(() => _answer = '');
      await ref.read(audioProvider).sfx(Sfx.error);
      if (mounted) showSnack(context, e.message, error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final cs = Theme.of(context).colorScheme;

    return Scaffold(
      backgroundColor: Colors.transparent,
      appBar: AppBar(title: const Text('Ask AccessAI')),
      body: MeshScaffoldBody(
        child: SafeArea(
          child: ContentWidth(
            child: ListView(
              padding: const EdgeInsets.all(T.s16),
              children: [
                Entrance(index: 0, child: _WakeWordCard()),
                const SizedBox(height: T.s20),
                Entrance(
                  index: 1,
                  child: Center(
                    child: AuroraRing(
                      size: 196,
                      thickness: 4,
                      active: _listening,
                      child: _MicButton(
                        listening: _listening,
                        enabled: _speechReady && !_busy,
                        onTap: _toggleListen,
                      ),
                    ),
                  ),
                ),
                const SizedBox(height: T.s16),
                Center(
                  child: AnimatedSwitcher(
                    duration: Motion.duration(context, T.med),
                    switchInCurve: T.easeExpo,
                    child: Text(
                      !_speechReady
                          ? 'Speech recognition unavailable — type below.'
                          : _listening
                              ? 'Listening…'
                              : 'Tap to ask a question',
                      key: ValueKey('$_speechReady-$_listening'),
                      style: text.titleMedium
                          ?.copyWith(color: cs.onSurfaceVariant),
                      textAlign: TextAlign.center,
                    ),
                  ),
                ),
                if (_partial.isNotEmpty) ...[
                  const SizedBox(height: T.s12),
                  Center(
                      child: Text('“$_partial”',
                          style: text.bodyLarge
                              ?.copyWith(fontStyle: FontStyle.italic))),
                ],
                const SizedBox(height: T.s24),
                // Discoverability: tap an example to ask it. Covers both fixed
                // intents and free-form visual questions (routed to the VLM).
                Entrance(
                  index: 2,
                  child: Semantics(
                    label: 'Example questions. Double-tap one to ask it.',
                    child: Wrap(
                      spacing: T.s8,
                      runSpacing: T.s8,
                      alignment: WrapAlignment.center,
                      children: [
                        for (final q in const [
                          'Who is at the door?',
                          'What is he doing now?',
                          'What colour is their dress?',
                          'Is anyone holding a package?',
                          'How many people are here?',
                        ])
                          ActionChip(
                            label: Text(q),
                            avatar: Icon(Icons.auto_awesome,
                                size: 16, color: T.jarvis2),
                            onPressed: _busy ? null : () => _ask(q),
                          ),
                      ],
                    ),
                  ),
                ),
                const SizedBox(height: T.s24),
                Entrance(
                  index: 3,
                  child: Row(
                    children: [
                      Expanded(
                        child: TextField(
                          controller: _typed,
                          textInputAction: TextInputAction.send,
                          onSubmitted: _ask,
                          decoration: const InputDecoration(
                            labelText: 'Or type a question',
                          ),
                        ),
                      ),
                      const SizedBox(width: T.s8),
                      SizedBox(
                        width: T.minTouch,
                        height: T.minTouch,
                        child: IconButton.filled(
                          onPressed: _busy ? null : () => _ask(_typed.text),
                          icon: const Icon(Icons.send),
                          tooltip: 'Ask',
                        ),
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: T.s24),
                AnimatedSwitcher(
                  duration: Motion.duration(context, T.med),
                  switchInCurve: T.easeExpo,
                  child: _busy
                      ? const Center(
                          key: ValueKey('busy'),
                          child: Padding(
                            padding: EdgeInsets.all(T.s16),
                            child: CircularProgressIndicator(),
                          ),
                        )
                      : _answer.isNotEmpty
                          ? GlassCard(
                              key: ValueKey(_answer),
                              borderTint: T.jarvis2,
                              glow: true,
                              child: Semantics(
                                liveRegion: true,
                                child: Column(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: [
                                    Row(
                                      children: [
                                        Container(
                                          width: 36,
                                          height: 36,
                                          decoration: BoxDecoration(
                                            borderRadius:
                                                BorderRadius.circular(T.rSm),
                                            gradient: LinearGradient(
                                              colors: [
                                                T.jarvis1
                                                    .withValues(alpha: 0.35),
                                                T.jarvis3
                                                    .withValues(alpha: 0.35),
                                              ],
                                            ),
                                          ),
                                          child: Icon(Icons.smart_toy_outlined,
                                              size: 20, color: T.jarvis1),
                                        ),
                                        const SizedBox(width: T.s12),
                                        Text('Answer', style: text.titleMedium),
                                        const Spacer(),
                                        IconButton(
                                          onPressed: () => ref
                                              .read(audioProvider)
                                              .speak(_answer,
                                                  ref.read(apiProvider)),
                                          icon: const Icon(Icons.volume_up),
                                          tooltip: 'Read aloud again',
                                        ),
                                      ],
                                    ),
                                    const SizedBox(height: T.s8),
                                    Text(_answer, style: text.bodyLarge),
                                  ],
                                ),
                              ),
                            )
                          : const SizedBox.shrink(key: ValueKey('empty')),
                ),
                const SizedBox(height: T.s24),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

/// The "hey access" always-on toggle. When on, the phone listens hands-free and
/// answers spoken questions without any tap (like "hey Siri"). Off by default —
/// an open mic is a battery + privacy choice the user opts into.
class _WakeWordCard extends ConsumerWidget {
  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final on = ref.watch(wakeWordProvider);
    final text = Theme.of(context).textTheme;
    final cs = Theme.of(context).colorScheme;
    return GlassCard(
      borderTint: on ? T.jarvis2 : null,
      glow: on,
      child: Semantics(
        toggled: on,
        label: 'Hey Access hands-free listening',
        child: Row(
          children: [
            Container(
              width: 44,
              height: 44,
              decoration: BoxDecoration(
                borderRadius: BorderRadius.circular(T.rSm),
                gradient: LinearGradient(
                  begin: Alignment.topLeft,
                  end: Alignment.bottomRight,
                  colors: on
                      ? [
                          T.jarvis1.withValues(alpha: 0.40),
                          T.jarvis3.withValues(alpha: 0.40),
                        ]
                      : [
                          cs.surfaceContainerHigh,
                          cs.surfaceContainer,
                        ],
                ),
              ),
              child: Icon(on ? Icons.hearing : Icons.hearing_disabled,
                  color: on ? T.jarvis1 : cs.onSurfaceVariant),
            ),
            const SizedBox(width: T.s12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('“Hey Access”', style: text.titleMedium),
                  const SizedBox(height: 2),
                  Text(
                    on
                        ? 'Listening hands-free. Say “Hey Access”, then your '
                            'question.'
                        : 'Turn on to ask without tapping, like “Hey Siri”.',
                    style: text.bodySmall
                        ?.copyWith(color: cs.onSurfaceVariant),
                  ),
                ],
              ),
            ),
            Switch(
              value: on,
              onChanged: (v) async {
                final ok = await ref.read(wakeWordProvider.notifier).setEnabled(v);
                if (!context.mounted) return;
                if (v && !ok) {
                  showSnack(context,
                      'Microphone unavailable. Grant mic permission to use '
                      'hands-free wake word.',
                      error: true);
                } else {
                  showSnack(context,
                      ok ? 'Hey Access is listening' : 'Hey Access off');
                }
              },
            ),
          ],
        ),
      ),
    );
  }
}

/// The big circular glass mic. Idle: green glass. Listening: aurora-lit with a
/// red stop glyph. Sits inside [AuroraRing], which supplies the breathing halo.
class _MicButton extends StatelessWidget {
  const _MicButton({
    required this.listening,
    required this.enabled,
    required this.onTap,
  });

  final bool listening;
  final bool enabled;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final color = listening ? T.danger : T.seed;
    return Semantics(
      button: true,
      label: listening ? 'Stop listening' : 'Start listening',
      enabled: enabled,
      child: Material(
        color: Colors.transparent,
        shape: const CircleBorder(),
        child: InkWell(
          customBorder: const CircleBorder(),
          onTap: enabled ? onTap : null,
          child: AnimatedContainer(
            duration: Motion.duration(context, T.med),
            curve: T.easeExpo,
            width: 132,
            height: 132,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              gradient: listening
                  ? LinearGradient(
                      begin: Alignment.topLeft,
                      end: Alignment.bottomRight,
                      colors: [
                        T.jarvis1.withValues(alpha: 0.35),
                        T.jarvis3.withValues(alpha: 0.35),
                      ],
                    )
                  : LinearGradient(
                      begin: Alignment.topLeft,
                      end: Alignment.bottomRight,
                      colors: [
                        color.withValues(alpha: 0.28),
                        color.withValues(alpha: 0.12),
                      ],
                    ),
              border:
                  Border.all(color: color.withValues(alpha: 0.6), width: 3),
              boxShadow: [
                BoxShadow(
                  color: color.withValues(alpha: listening ? 0.35 : 0.18),
                  blurRadius: 30,
                  spreadRadius: -4,
                ),
              ],
            ),
            child: Icon(listening ? Icons.stop : Icons.mic,
                size: 56, color: color),
          ),
        ),
      ),
    );
  }
}
