import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../core/theme.dart';
import '../models/app_status.dart';
import '../models/known_person.dart';
import '../models/visitor_event.dart';
import '../services/api_service.dart';
import '../services/audio_service.dart';
import '../services/background_alert_service.dart';
import '../services/events_service.dart';
import '../services/notification_service.dart';
import '../services/prefs_service.dart';
import '../services/wakeword_service.dart';

/// Overridden in main() once SharedPreferences has loaded.
final sharedPreferencesProvider =
    Provider<SharedPreferences>((ref) => throw UnimplementedError());

final prefsProvider = Provider<PrefsService>(
    (ref) => PrefsService(ref.watch(sharedPreferencesProvider)));

// --- Server base URL ------------------------------------------------------
class BaseUrlNotifier extends Notifier<String> {
  @override
  String build() => ref.read(prefsProvider).baseUrl;

  Future<void> set(String url) async {
    final prefs = ref.read(prefsProvider);
    await prefs.setBaseUrl(url);
    state = prefs.baseUrl; // normalized form
  }
}

final baseUrlProvider =
    NotifierProvider<BaseUrlNotifier, String>(BaseUrlNotifier.new);

// --- Bearer token (Phase 17) -----------------------------------------------
class AuthTokenNotifier extends Notifier<String> {
  @override
  String build() => ref.read(prefsProvider).authToken;

  Future<void> set(String token) async {
    await ref.read(prefsProvider).setAuthToken(token);
    state = token.trim();
  }
}

final authTokenProvider =
    NotifierProvider<AuthTokenNotifier, String>(AuthTokenNotifier.new);

/// Whether the user has confirmed a server URL at least once (drives first-run).
final isConfiguredProvider = Provider<bool>((ref) {
  ref.watch(baseUrlProvider); // recompute after a save
  return ref.watch(prefsProvider).isConfigured;
});

// --- REST client (recreated when the base URL or token changes) ------------
final apiProvider = Provider<ApiService>((ref) {
  final base = ref.watch(baseUrlProvider);
  final token = ref.watch(authTokenProvider);
  // Stable per-install id so this phone can hold its own mode (Phase 17).
  final deviceId = ref.read(prefsProvider).deviceId;
  final api = ApiService(base, token: token, deviceId: deviceId);
  ref.onDispose(api.close);
  return api;
});

// --- Live events WebSocket ------------------------------------------------
final eventsServiceProvider = Provider<EventsService>((ref) {
  final api = ref.watch(apiProvider);
  final svc = EventsService(api.eventsWsUri);
  svc.connect();
  ref.onDispose(svc.dispose);
  return svc;
});

final eventStreamProvider = StreamProvider<Map<String, dynamic>>(
    (ref) => ref.watch(eventsServiceProvider).events);

final wsStateProvider = StreamProvider<WsState>(
    (ref) => ref.watch(eventsServiceProvider).state);

// --- Background doorbell alerts (LAN-only, no Firebase) -------------------
/// Posts the actual notification. Initialised once in main() before the first
/// frame so an event arriving during startup still alerts.
final notificationServiceProvider = Provider<NotificationService>(
    (ref) => NotificationService());

/// Keeps the MAIN isolate alive (and with it the /events socket) while the app
/// is backgrounded. See background_alert_service.dart for why the socket is
/// deliberately NOT moved into the service's own isolate.
final backgroundAlertServiceProvider = Provider<BackgroundAlertService>(
    (ref) => BackgroundAlertService());

/// Whether background alerts are switched on. Driving the foreground service
/// from a notifier (not the Settings widget) means it survives tab switches and
/// resumes on launch exactly like the wake word does.
class BackgroundAlertsNotifier extends Notifier<bool> {
  @override
  bool build() {
    final enabled = ref.read(prefsProvider).backgroundAlertsEnabled;
    if (enabled) {
      // Defer: providers must finish building before we touch platform channels.
      Future.microtask(() => _start());
    }
    return enabled;
  }

  Future<bool> setEnabled(bool on) async {
    await ref.read(prefsProvider).setBackgroundAlertsEnabled(on);
    if (!on) {
      await ref.read(backgroundAlertServiceProvider).stop();
      state = false;
      return false;
    }
    return _start();
  }

  /// Starts the service, but only after notification permission is actually
  /// granted — a foreground service with no postable notification would show
  /// the mandatory persistent notice and still alert nothing.
  Future<bool> _start() async {
    final notif = ref.read(notificationServiceProvider);
    await notif.init();
    if (!notif.granted) {
      state = false;
      return false;
    }
    final ok = await ref.read(backgroundAlertServiceProvider).start();
    state = ok;
    return ok;
  }
}

final backgroundAlertsProvider =
    NotifierProvider<BackgroundAlertsNotifier, bool>(
        BackgroundAlertsNotifier.new);

// --- Audio / haptics singleton -------------------------------------------
final audioProvider = Provider<AudioService>((ref) {
  final a = AudioService();
  ref.onDispose(a.dispose);
  return a;
});

// --- On-device "hey access" wake word -------------------------------------
/// The single always-on listener instance for the app's lifetime.
final wakeWordServiceProvider = Provider<WakeWordService>((ref) {
  final svc = WakeWordService();
  ref.onDispose(svc.dispose);
  return svc;
});

/// Drives + reflects whether the on-device wake word is actively listening.
/// Wiring the mic->/command->speak flow lives in the notifier so it works no
/// matter which screen is showing (true hands-free, like "hey Siri").
class WakeWordNotifier extends Notifier<bool> {
  @override
  bool build() {
    // Auto-start if the user left it enabled last session.
    final enabled = ref.read(prefsProvider).wakeWordEnabled;
    if (enabled) {
      // Defer so providers finish building before we touch the mic.
      Future.microtask(() => _enable(persist: false));
    }
    return enabled;
  }

  Future<bool> setEnabled(bool on) async {
    await ref.read(prefsProvider).setWakeWordEnabled(on);
    if (on) {
      return _enable(persist: false);
    } else {
      await ref.read(wakeWordServiceProvider).stop();
      state = false;
      return false;
    }
  }

  Future<bool> _enable({required bool persist}) async {
    final svc = ref.read(wakeWordServiceProvider);
    final ok = await svc.init();
    if (!ok) {
      state = false;
      return false;
    }
    // Wire the wake -> ask -> speak flow once.
    svc.onCommand = (question) async {
      final audio = ref.read(audioProvider);
      try {
        await audio.sfx(Sfx.thinking); // heard: "working on it"
        final res = await ref.read(apiProvider).command(question);
        final answer = (res['answer'] ?? '').toString().trim();
        // ALWAYS speak something back: silence after a wake word reads as
        // "Jarvis is not responding" to a blind user.
        await audio.speak(
            answer.isNotEmpty
                ? answer
                : "I didn't get an answer for that. Please try again.",
            ref.read(apiProvider));
      } catch (_) {
        // Offline / server error: error earcon + a short spoken reason.
        await audio.sfx(Sfx.error);
        await audio.speak(
            "I couldn't reach the doorbell server. Please check the "
            'connection in Settings.',
            ref.read(apiProvider));
      }
    };
    svc.onWake = () {
      final audio = ref.read(audioProvider);
      audio.sfx(Sfx.wake); // the Jarvis "I'm awake" chime
      audio.announceOnly('Yes?');
    };
    await svc.start();
    state = true;
    return true;
  }
}

final wakeWordProvider =
    NotifierProvider<WakeWordNotifier, bool>(WakeWordNotifier.new);

// --- Accessibility mode (blind|deaf|both), synced with the backend --------
class ModeNotifier extends Notifier<String> {
  @override
  String build() => ref.read(prefsProvider).mode;

  Future<void> set(String m) async {
    if (m != 'blind' && m != 'deaf' && m != 'both') return;
    state = m;
    await ref.read(prefsProvider).setMode(m);
    try {
      await ref.read(apiProvider).setMode(m);
    } catch (_) {
      // Offline is fine; the local mode still governs the phone's behaviour.
    }
  }

  /// Adopt the server's mode without re-POSTing (used on first load).
  void adopt(String m) {
    if ((m == 'blind' || m == 'deaf' || m == 'both') && m != state) {
      state = m;
      ref.read(prefsProvider).setMode(m);
    }
  }
}

final modeProvider = NotifierProvider<ModeNotifier, String>(ModeNotifier.new);

// --- Quick replies (Phase 17) ---------------------------------------------
// Canned one-tap replies, fetched from the server so the household can edit
// them in one place. The API layer already falls back to a built-in set, so
// this never resolves empty.
final quickRepliesProvider = FutureProvider<List<String>>(
    (ref) => ref.watch(apiProvider).quickReplies());

// --- Theme choice ---------------------------------------------------------
class ThemeNotifier extends Notifier<AppThemeChoice> {
  @override
  AppThemeChoice build() => ref.read(prefsProvider).themeChoice;

  Future<void> set(AppThemeChoice c) async {
    state = c;
    await ref.read(prefsProvider).setThemeChoice(c);
  }
}

final themeProvider =
    NotifierProvider<ThemeNotifier, AppThemeChoice>(ThemeNotifier.new);

// --- Data providers (auto-refresh when the API/base URL changes) ----------
/// Live search text for the History screen; historyProvider re-fetches with
/// the server-side ?q= filter whenever this changes.
class HistorySearchNotifier extends Notifier<String> {
  @override
  String build() => '';
  void set(String q) => state = q;
}

final historySearchProvider =
    NotifierProvider<HistorySearchNotifier, String>(HistorySearchNotifier.new);

final historyProvider = FutureProvider<List<VisitorEvent>>((ref) async {
  final q = ref.watch(historySearchProvider);
  return ref.watch(apiProvider).history(limit: 50, q: q);
});

final knownProvider = FutureProvider<List<KnownPerson>>(
    (ref) async => ref.watch(apiProvider).known());

final statusProvider = FutureProvider<AppStatus>(
    (ref) async => ref.watch(apiProvider).status());

/// The most recent event seen (via /trigger response or a live WS push). Drives
/// the Home hero card without waiting for a history reload.
class LatestEventNotifier extends Notifier<VisitorEvent?> {
  @override
  VisitorEvent? build() => null;

  void set(VisitorEvent? e) => state = e;
}

final latestEventProvider =
    NotifierProvider<LatestEventNotifier, VisitorEvent?>(
        LatestEventNotifier.new);

/// The event id of a doorbell the user just rang from the app. The live-alert
/// listener skips the full-screen takeover for this id (the Home card already
/// updated) so a manual test doesn't hijack the screen; real incoming visitors
/// still alert normally.
class SelfTriggeredIdNotifier extends Notifier<String?> {
  @override
  String? build() => null;

  void set(String? id) => state = id;
}

final selfTriggeredIdProvider =
    NotifierProvider<SelfTriggeredIdNotifier, String?>(
        SelfTriggeredIdNotifier.new);
