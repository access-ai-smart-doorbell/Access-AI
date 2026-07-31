import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'app.dart';
import 'services/notification_service.dart';
import 'state/providers.dart';

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

  runApp(
    ProviderScope(
      overrides: [
        sharedPreferencesProvider.overrideWithValue(prefs),
        notificationServiceProvider.overrideWithValue(notifications),
      ],
      child: const AccessAIApp(),
    ),
  );
}
