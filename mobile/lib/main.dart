import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'app.dart';
import 'services/background_alert_service.dart';
import 'services/notification_service.dart';
import 'state/providers.dart';

/// Global container reference so we can reach providers from outside the tree
/// (specifically from the foreground service port listener below).
ProviderContainer? _container;

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();

  // SharedPreferences is the one dependency that must be ready before the first
  // frame (it seeds the server URL, mode, and theme). Everything else is lazy.
  final prefs = await SharedPreferences.getInstance();

  // Notifications are the second: with background alerts on, the app can be
  // launched straight into the background by the boot receiver, and a doorbell
  // arriving during startup must have somewhere to go. init() is safe to call
  // before any UI exists and no-ops if the user denies permission.
  final notifications = NotificationService();
  await notifications.init();

  // Register the IsolateNameServer port so the foreground service worker can
  // signal the main isolate (e.g. "started" / "heartbeat"). This lets us
  // verify the wake word loop is alive and restart it if needed — the same
  // trick Siri uses to keep its recognizer warm via a system daemon.
  BackgroundAlertService.initCommunicationPort((dynamic msg) {
    if (msg == 'started' || msg == 'heartbeat') {
      // Service is alive. If the user has wake word enabled, ensure it's
      // running — it may have been killed by an aggressive OEM task manager.
      final container = _container;
      if (container == null) return;
      final wakeEnabled =
          container.read(prefsProvider).wakeWordEnabled;
      if (wakeEnabled) {
        final wakeState = container.read(wakeWordProvider);
        if (!wakeState) {
          // Wake word was killed in background — restart it silently.
          container.read(wakeWordProvider.notifier).setEnabled(true);
        }
      }
    }
  });

  final container = ProviderContainer(overrides: [
    sharedPreferencesProvider.overrideWithValue(prefs),
    notificationServiceProvider.overrideWithValue(notifications),
  ]);
  _container = container;

  runApp(
    UncontrolledProviderScope(
      container: container,
      child: const AccessAIApp(),
    ),
  );
}
