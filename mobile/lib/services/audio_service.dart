import 'dart:async';
import 'dart:io';
import 'dart:ui' as ui;

import 'package:flutter/foundation.dart';
import 'package:flutter/semantics.dart';
import 'package:flutter_tts/flutter_tts.dart';
import 'package:just_audio/just_audio.dart';
import 'package:vibration/vibration.dart';

import 'api_service.dart';

/// The Jarvis UI sound set (assets/audio/, synthesized by tool/gen_sfx.py).
enum Sfx {
  wake('assets/audio/jarvis_wake.wav'),
  listen('assets/audio/jarvis_listen.wav'),
  success('assets/audio/jarvis_success.wav'),
  error('assets/audio/jarvis_error.wav'),
  thinking('assets/audio/jarvis_thinking.wav'),
  doorbell('assets/audio/doorbell.wav');

  const Sfx(this.asset);
  final String asset;
}

/// Speech + haptics for accessibility output.
///
/// Blind / Both mode → speak the announcement. Preferred path is the server's
/// natural Kokoro voice via GET /speak_audio (played with just_audio); if that
/// 503s or fails, we fall back to on-device flutter_tts. Either way we also push
/// the text through SemanticsService.announce so a screen reader voices it too.
///
/// Deaf / Both mode → vibrate (a distinct doorbell pattern) so the alert is felt.
///
/// Jarvis sound cues → short earcons played on a dedicated player so they never
/// interrupt speech: wake chime, listening blip, success/error, doorbell.
class AudioService {
  final AudioPlayer _player = AudioPlayer();
  final AudioPlayer _sfxPlayer = AudioPlayer();
  final FlutterTts _tts = FlutterTts();
  bool _ttsReady = false;

  /// Bumped by every speak()/stop() so an in-flight chunked utterance knows it
  /// was superseded and quietly abandons its remaining chunks.
  int _speakSession = 0;

  /// Rolls per written chunk so each temp WAV has a unique path — ExoPlayer
  /// caches by file path, so reusing one name would replay stale audio.
  int _chunkFile = 0;

  Future<void> _ensureTts() async {
    if (_ttsReady) return;
    try {
      await _tts.setSpeechRate(0.48);
      await _tts.setVolume(1.0);
      await _tts.setPitch(1.0);
    } catch (_) {}
    _ttsReady = true;
  }

  /// Speak [text]. Tries the server's Kokoro WAV first, then on-device TTS.
  /// Returns the path that actually spoke, for reporting/telemetry.
  /// Never throws.
  ///
  /// Long text (a VLM scene description) is split into sentence chunks and
  /// streamed: the FIRST chunk is synthesized and starts playing within a
  /// couple of seconds, while the rest synthesizes in the background. Kokoro's
  /// synthesis time grows with text length, so waiting for one giant WAV made
  /// descriptions take 10 s+ before the first word was heard.
  Future<String> speak(String text, ApiService api) async {
    final t = text.trim();
    if (t.isEmpty) return 'empty';
    final session = ++_speakSession;

    // Screen-reader announcement (independent of audible speech).
    _announce(t);

    // 1) Server Kokoro voice, sentence-chunked. Bytes are fetched with Dio
    // (30 s receive timeout) instead of _player.setUrl — ExoPlayer's own HTTP
    // fetch times out at ~8 s, which long syntheses exceed, silently dropping
    // us to the robotic on-device voice.
    Future<Uint8List>? next;
    try {
      final chunks = _chunkSentences(t);
      // Prefetch pipeline: while chunk N plays, chunk N+1 is synthesizing.
      next = api.speakAudio(chunks.first);
      for (var i = 0; i < chunks.length; i++) {
        final wav = await next!;
        next = i + 1 < chunks.length ? api.speakAudio(chunks[i + 1]) : null;
        if (session != _speakSession) {
          next?.ignore(); // superseded: drop the prefetch quietly
          return 'kokoro';
        }
        await _player.stop();
        await _player.setFilePath(await _writeChunk(wav));
        await _player.play(); // completes when the chunk finishes
      }
      return 'kokoro';
    } catch (_) {
      next?.ignore(); // don't leak an unhandled prefetch error
      // fall through to on-device TTS
    }

    // 2) On-device TTS fallback.
    if (session != _speakSession) return 'none';
    try {
      await _ensureTts();
      await _tts.stop();
      await _tts.speak(t);
      return 'flutter_tts';
    } catch (_) {
      return 'none';
    }
  }

  /// Split [text] into speakable chunks of one-or-more sentences, each roughly
  /// under 140 chars, so the first audio arrives fast. Short text stays whole.
  static List<String> _chunkSentences(String text) {
    if (text.length <= 160) return [text];
    final sentences = text.split(RegExp(r'(?<=[.!?])\s+'));
    final chunks = <String>[];
    var current = StringBuffer();
    for (final s in sentences) {
      if (current.isNotEmpty && current.length + s.length > 140) {
        chunks.add(current.toString());
        current = StringBuffer();
      }
      if (current.isNotEmpty) current.write(' ');
      current.write(s);
    }
    if (current.isNotEmpty) chunks.add(current.toString());
    return chunks.isEmpty ? [text] : chunks;
  }

  /// Write one synthesized WAV chunk to a uniquely-named temp file and return
  /// its path. just_audio plays from a file (setFilePath) rather than an
  /// in-memory StreamAudioSource (which is marked experimental); a fresh name
  /// each time defeats ExoPlayer's path-based cache.
  Future<String> _writeChunk(Uint8List wav) async {
    final path =
        '${Directory.systemTemp.path}/accessai_speak_${_chunkFile++}.wav';
    await File(path).writeAsBytes(wav, flush: true);
    return path;
  }

  void _announce(String text) {
    try {
      final view = ui.PlatformDispatcher.instance.implicitView;
      if (view != null) {
        SemanticsService.sendAnnouncement(view, text, TextDirection.ltr);
      }
    } catch (_) {}
  }

  /// Announce to assistive tech only (no audible speech) — used for status
  /// changes like "Listening" where we don't want to talk over the visitor.
  void announceOnly(String text) => _announce(text);

  /// Play a Jarvis earcon. Runs on its own player so it can layer over (or
  /// precede) speech without cutting it off. Never throws; audio cues are
  /// always best-effort decoration, not functionality.
  Future<void> sfx(Sfx cue) async {
    try {
      await _sfxPlayer.stop();
      await _sfxPlayer.setAsset(cue.asset);
      unawaited(_sfxPlayer.play());
    } catch (e) {
      debugPrint('sfx ${cue.name} failed: $e');
    }
  }

  Future<void> stop() async {
    _speakSession++; // abandon any in-flight chunked utterance
    try {
      await _player.stop();
    } catch (_) {}
    try {
      await _sfxPlayer.stop();
    } catch (_) {}
    try {
      await _tts.stop();
    } catch (_) {}
  }

  /// A distinct doorbell haptic (long-short-short). Falls back to a single
  /// buzz if pattern vibration isn't supported. Never throws.
  Future<void> doorbellVibrate() async {
    try {
      final hasVibrator = await Vibration.hasVibrator();
      if (!hasVibrator) return;
      final hasPattern = await Vibration.hasCustomVibrationsSupport();
      if (hasPattern) {
        await Vibration.vibrate(
            pattern: const [0, 450, 160, 180, 160, 180], intensities: const []);
      } else {
        await Vibration.vibrate(duration: 500);
      }
    } catch (e) {
      debugPrint('vibrate failed: $e');
    }
  }

  /// A LARGE, unmissable vibration for the VLM visitor description arriving —
  /// three long, full-strength pulses, clearly distinct from the doorbell's
  /// long-short-short. Falls back to a single long buzz. Never throws.
  Future<void> descriptionVibrate() async {
    try {
      final hasVibrator = await Vibration.hasVibrator();
      if (!hasVibrator) return;
      final hasPattern = await Vibration.hasCustomVibrationsSupport();
      if (hasPattern) {
        await Vibration.vibrate(
          pattern: const [0, 700, 250, 700, 250, 700],
          intensities: const [0, 255, 0, 255, 0, 255],
        );
      } else {
        await Vibration.vibrate(duration: 1200);
      }
    } catch (e) {
      debugPrint('description vibrate failed: $e');
    }
  }

  /// Phase 17: per-visitor-type haptics keyed by VisitorEvent.kind. Distinct
  /// rhythms a deaf (or deafblind) user can tell apart in a pocket:
  ///   known    — short-long ("ta-da", friendly)
  ///   delivery — three quick even taps ("knock knock knock")
  ///   unknown  — one plain long buzz
  ///   spoof    — three LONG full-strength pulses (unmistakably a warning)
  /// Unknown kinds fall back to the classic doorbell pattern; devices without
  /// pattern support get one buzz (long for spoof). Never throws.
  Future<void> kindVibrate(String kind) async {
    const patterns = <String, List<int>>{
      'known': [0, 150, 80, 400],
      'delivery': [0, 130, 90, 130, 90, 130],
      'unknown': [0, 500],
      'spoof': [0, 650, 220, 650, 220, 650],
    };
    final pattern = patterns[kind];
    if (pattern == null) return doorbellVibrate();
    try {
      final hasVibrator = await Vibration.hasVibrator();
      if (!hasVibrator) return;
      final hasPattern = await Vibration.hasCustomVibrationsSupport();
      if (hasPattern) {
        await Vibration.vibrate(
          pattern: pattern,
          // Spoof pulses run at full strength — it is a warning, not a chime.
          intensities: kind == 'spoof'
              ? List<int>.generate(pattern.length, (i) => i.isEven ? 0 : 255)
              : const [],
        );
      } else {
        await Vibration.vibrate(duration: kind == 'spoof' ? 1200 : 500);
      }
    } catch (e) {
      debugPrint('kindVibrate($kind) failed: $e');
    }
  }

  /// A short success tap for confirming an action (e.g. Ring sent).
  Future<void> successTap() async {
    try {
      if (await Vibration.hasVibrator()) {
        await Vibration.vibrate(duration: 60);
      }
    } catch (_) {}
  }

  Future<void> dispose() async {
    await _player.dispose();
    try {
      await _sfxPlayer.dispose();
    } catch (_) {}
    try {
      await _tts.stop();
    } catch (_) {}
  }
}
