import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:speech_to_text/speech_to_text.dart' as stt;

/// On-device "hey access" wake word — the phone's own always-on listener, so a
/// Blind user can ask hands-free like "hey Siri", WITHOUT holding the phone or
/// tapping the mic.
///
/// WHY on-device (not the backend's openWakeWord): the wake word must fire from
/// the PHONE the user is holding, using the phone's mic. The backend wake engine
/// listens on the *computer's* mic at the door, which is a different use-case.
///
/// HOW it works, deliberately dependency-light — it reuses the `speech_to_text`
/// plugin the app already ships (device STT, offline-capable, no API key, no
/// Porcupine AccessKey friction):
///   1. Continuously listen in short windows (STT auto-stops on silence).
///   2. Each transcript is scanned for a wake phrase ("hey access", with
///      "hey jarvis" kept as a legacy alias).
///   3. On a hit, whatever the user said AFTER the wake word in the same
///      utterance is taken as the question; if nothing followed, we arm a short
///      "command capture" window and use the next utterance as the question.
///   4. The question is handed to [onCommand], which POSTs it to /command (which
///      now answers both fixed intents AND free-form visual questions).
///   5. Re-arm and keep listening.
///
/// This is a pragmatic wake word: device STT is not a dedicated always-on DSP
/// keyword spotter, so it costs more battery than Porcupine would and can miss
/// in loud rooms. It needs no cloud, no key, and no extra native code, which is
/// the right trade for a first cut. The engine is OPT-IN (Settings) and fully
/// stoppable; callers must ensure mic permission is granted first.
class WakeWordService {
  WakeWordService();

  final stt.SpeechToText _speech = stt.SpeechToText();

  /// Wake phrases we accept, LONGEST FIRST within each group (on an equal match
  /// position, [_afterWake] takes the first list entry — longest-first stops a
  /// prefix like "hey access" from shadowing "hey accessed" and leaving "ed"
  /// behind as a bogus command). "Hey Access" is the product wake word (the
  /// backend now runs a custom-trained hey_access model too); the jarvis set is
  /// kept as a legacy alias. Two-word forms are matched loosely because device
  /// STT often mishears them ("access" -> "axis"/"excess") and we would rather
  /// over-trigger than make a Blind user repeat themselves. Bare single words
  /// are allowed ONLY for coined words like "jarvis" — bare "access"/"axis" are
  /// common English words and would false-wake on ordinary speech.
  static const List<String> _wakeVariants = [
    // primary: "Hey Access" + common STT mishearings of the two-word phrase
    'hey accessed', 'hey access', 'hey assess', 'hey excess', 'hay access',
    'hey acces', 'hey axis',
    // legacy alias: "Hey Jarvis" + mishearings (bare forms OK: coined word)
    'hey charvis', 'hey jarvis', 'hey jervis', 'hey travis',
    'jaravis', 'jarvis', 'jervis', 'jarvit', 'jarwis',
  ];

  bool _ready = false;
  bool _enabled = false; // user intent (should we be listening at all)
  bool _running = false; // a listen session is currently live
  bool _awaitingCommand = false; // wake heard, capturing the follow-up question
  int _errorStreak = 0; // consecutive engine errors -> forces a re-initialize
  Timer? _rearm;
  Timer? _watchdog;

  /// Called with the recognised question text after a wake word. Return value is
  /// ignored; the callback owns sending it to the backend and speaking the reply.
  Future<void> Function(String question)? onCommand;

  /// Optional lifecycle hooks for UI/announcements.
  void Function()? onWake; // wake word detected (before the question)
  void Function(bool listening)? onListeningChanged;

  bool get isEnabled => _enabled;
  bool get isRunning => _running;
  bool get isReady => _ready;

  /// Initialise device speech recognition. Safe to call repeatedly. Returns
  /// whether STT is usable on this device (mic permission + engine present).
  Future<bool> init() async {
    if (_ready) return true;
    try {
      _ready = await _speech.initialize(
        onStatus: _onStatus,
        onError: _onError,
      );
    } catch (e) {
      debugPrint('[wakeword] init failed: $e');
      _ready = false;
    }
    if (_ready) _errorStreak = 0;
    return _ready;
  }

  void _onError(dynamic err) {
    // Errors (no-match, timeout, transient engine faults) are expected in an
    // always-on loop. Before re-arming, salvage the last partial transcript:
    // Android often ends a session with error_speech_timeout WITHOUT ever
    // delivering a finalResult, which used to silently swallow a heard
    // "hey access". Repeated errors force a full engine re-initialize.
    _running = false;
    onListeningChanged?.call(false);
    final salvage = _lastPartial.trim();
    _lastPartial = '';
    if (salvage.isNotEmpty &&
        (_awaitingCommand || _wakeIndex(salvage.toLowerCase()) >= 0)) {
      _errorStreak = 0;
      _handleUtterance(salvage, wokeViaPartial: _wokeThisSession);
      _wokeThisSession = false;
      return; // _dispatch / arming re-arms the loop itself
    }
    _wokeThisSession = false;
    _errorStreak++;
    if (_errorStreak >= 6) {
      // The engine is wedged (e.g. error_busy loop) — rebuild it on re-arm.
      _ready = false;
      _errorStreak = 0;
    }
    _scheduleRearm(const Duration(milliseconds: 400));
  }

  /// Turn the always-on listener on. Idempotent. [init] must have succeeded.
  Future<void> start() async {
    if (!_ready) {
      final ok = await init();
      if (!ok) return;
    }
    _enabled = true;
    // Safety net: if both status + error callbacks are ever missed (engine
    // hang), the watchdog restarts the listen session so the loop can't die.
    _watchdog?.cancel();
    _watchdog = Timer.periodic(const Duration(seconds: 12), (_) {
      if (_enabled && !_running) _listenOnce();
    });
    _listenOnce();
  }

  /// Turn the listener off and cancel any pending re-arm.
  Future<void> stop() async {
    _enabled = false;
    _awaitingCommand = false;
    _watchdog?.cancel();
    _watchdog = null;
    _rearm?.cancel();
    _rearm = null;
    try {
      await _speech.stop();
    } catch (_) {}
    _running = false;
    onListeningChanged?.call(false);
  }

  void _onStatus(String status) {
    if (status == 'done' || status == 'notListening') {
      _running = false;
      onListeningChanged?.call(false);
      // Same salvage as _onError: some engines end a session ('done') with a
      // partial transcript but no finalResult callback. Don't lose a heard wake.
      final salvage = _lastPartial.trim();
      _lastPartial = '';
      if (salvage.isNotEmpty &&
          (_awaitingCommand || _wakeIndex(salvage.toLowerCase()) >= 0)) {
        _handleUtterance(salvage, wokeViaPartial: _wokeThisSession);
        _wokeThisSession = false;
        return;
      }
      _wokeThisSession = false;
      // Keep the loop alive while enabled: re-arm after the engine settles.
      if (_enabled) _scheduleRearm(const Duration(milliseconds: 300));
    }
  }

  void _scheduleRearm(Duration d) {
    if (!_enabled) return;
    _rearm?.cancel();
    _rearm = Timer(d, () {
      if (_enabled && !_running) _listenOnce();
    });
  }

  /// The newest partial transcript of the live session — salvaged when the
  /// engine ends without ever delivering a finalResult (common on Android).
  String _lastPartial = '';

  /// Whether the wake chime already fired from a PARTIAL result this session,
  /// so the final result doesn't chime a second time.
  bool _wokeThisSession = false;

  Future<void> _listenOnce() async {
    if (!_enabled || _running || !_ready) return;
    _running = true;
    _lastPartial = '';
    _wokeThisSession = false;
    onListeningChanged?.call(true);
    try {
      await _speech.listen(
        onResult: (r) {
          final words = r.recognizedWords.trim();
          if (words.isEmpty) return;
          if (r.finalResult) {
            _lastPartial = ''; // consumed here; nothing to salvage later
            _handleUtterance(words, wokeViaPartial: _wokeThisSession);
            _wokeThisSession = false;
          } else {
            _lastPartial = words;
            // Chime the moment the wake word shows up in a partial, so the
            // user knows they were heard even before the engine finalises.
            if (!_awaitingCommand &&
                !_wokeThisSession &&
                _wakeIndex(words.toLowerCase()) >= 0) {
              _wokeThisSession = true;
              onWake?.call();
            }
          }
        },
        listenOptions: stt.SpeechListenOptions(
          // Command-capture windows can run a little longer than wake windows.
          listenFor: Duration(seconds: _awaitingCommand ? 12 : 8),
          pauseFor: const Duration(seconds: 3),
          partialResults: true,
          cancelOnError: true,
        ),
      );
    } catch (e) {
      debugPrint('[wakeword] listen failed: $e');
      _running = false;
      onListeningChanged?.call(false);
      _scheduleRearm(const Duration(milliseconds: 500));
    }
  }

  void _handleUtterance(String words, {bool wokeViaPartial = false}) {
    final lower = words.toLowerCase();

    // Already woken and waiting for the question → this utterance IS the command.
    if (_awaitingCommand) {
      _awaitingCommand = false;
      final q = _stripWake(lower, words);
      if (q.trim().isNotEmpty) {
        _dispatch(q.trim());
      } else if (_enabled) {
        _scheduleRearm(const Duration(milliseconds: 300));
      }
      return;
    }

    final idx = _wakeIndex(lower);
    if (idx < 0) {
      // No wake word; make sure the loop re-arms even when we were called from
      // a salvage path (the normal path re-arms via onStatus).
      if (_enabled && !_running) {
        _scheduleRearm(const Duration(milliseconds: 300));
      }
      return;
    }

    if (!wokeViaPartial) onWake?.call();
    // Is there a question in the same breath ("jarvis, what colour is his shirt")?
    final after = _afterWake(words, lower, idx);
    if (after.trim().length >= 2) {
      _dispatch(after.trim());
    } else {
      // Bare wake word → capture the next utterance as the command. Re-arm
      // NOW so the capture window opens promptly even from a salvage path.
      _awaitingCommand = true;
      if (_enabled && !_running) {
        _scheduleRearm(const Duration(milliseconds: 250));
      }
    }
  }

  /// Index of the earliest wake variant in [lower], or -1. Longest (most
  /// specific) variants are checked first so "hey accessed" wins over "hey access".
  int _wakeIndex(String lower) {
    var best = -1;
    for (final w in _wakeVariants) {
      final i = lower.indexOf(w);
      if (i >= 0 && (best < 0 || i < best)) best = i;
    }
    return best;
  }

  /// The text following the matched wake word in the original-cased [words].
  String _afterWake(String words, String lower, int idx) {
    // Find which variant matched at/after idx and skip past it.
    var end = idx;
    for (final w in _wakeVariants) {
      final i = lower.indexOf(w);
      if (i == idx) {
        end = i + w.length;
        break;
      }
    }
    if (end >= words.length) return '';
    var rest = words.substring(end);
    // Trim a leading comma/space the user may have paused with.
    rest = rest.replaceFirst(RegExp(r'^[\s,\.]+'), '');
    return rest;
  }

  /// Remove any leading wake word from a follow-up utterance (users often repeat
  /// "jarvis" at the start of the command).
  String _stripWake(String lower, String words) {
    final idx = _wakeIndex(lower);
    if (idx == 0) return _afterWake(words, lower, 0);
    return words;
  }

  Future<void> _dispatch(String question) async {
    // Pause the loop while the command is handled so we don't record our own
    // spoken answer; the callback re-enables listening implicitly via re-arm.
    try {
      await _speech.stop();
    } catch (_) {}
    _running = false;
    try {
      await onCommand?.call(question);
    } catch (e) {
      debugPrint('[wakeword] onCommand failed: $e');
    }
    // Resume the wake loop after the answer has been dispatched.
    if (_enabled) _scheduleRearm(const Duration(milliseconds: 600));
  }

  Future<void> dispose() async {
    _watchdog?.cancel();
    _rearm?.cancel();
    try {
      await _speech.stop();
    } catch (_) {}
  }
}
