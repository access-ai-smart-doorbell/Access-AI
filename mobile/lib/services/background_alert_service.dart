import 'dart:async';
import 'dart:isolate';
import 'dart:ui';

import 'package:flutter_foreground_task/flutter_foreground_task.dart';

import 'notification_service.dart';

/// Keeps the phone reachable by the doorbell while the app is closed.
///
/// WHY a foreground service at all: Android freezes a backgrounded process
/// within a minute or two, which silently kills the /events WebSocket. A
/// doorbell that only works while you are staring at the app is not a doorbell.
/// A foreground service is the one sanctioned way to hold a socket open
/// indefinitely, and it is honest about it -- Android forces a persistent
/// notification the user can see and stop.
///
/// WHY microphone type: Android 14+ requires the foreground service type to
/// declare "microphone" if it touches the mic at all. Without it, wake word
/// recording silently fails with a SecurityException. We declare both
/// dataSync (for the WebSocket) and microphone (for "Hey Access").
///
/// This wrapper deliberately does NOT run the socket in the service's isolate.
/// The isolate would need its own copy of the API config, auth token, TTS and
/// Riverpod graph, and would then race the UI isolate over the same event
/// stream. Instead the service exists purely to keep the MAIN isolate alive,
/// where [EventsService] already runs and [NotificationService] already posts
/// alerts. One socket, one code path, no duplicate notifications.
class BackgroundAlertService {
  BackgroundAlertService();

  bool _initialised = false;

  /// Port name used to signal the main isolate from the service worker.
  static const String _portName = 'accessai_bg_port';

  /// Whether the service is currently running (best-effort; false on non-Android).
  Future<bool> get isRunning async {
    try {
      return await FlutterForegroundTask.isRunningService;
    } catch (_) {
      return false;
    }
  }

  void _initOnce() {
    if (_initialised) return;
    FlutterForegroundTask.init(
      androidNotificationOptions: AndroidNotificationOptions(
        channelId: NotificationService.serviceChannelId,
        channelName: 'Listening for the doorbell',
        channelDescription:
            'Keeps the connection to your AccessAI server open so alerts '
            'arrive when the app is closed.',
        // LOW: the notice must be visible (Android requires it) but must not
        // buzz -- the doorbell alerts themselves are what should interrupt.
        channelImportance: NotificationChannelImportance.LOW,
        priority: NotificationPriority.LOW,
        onlyAlertOnce: true,
      ),
      iosNotificationOptions: const IOSNotificationOptions(),
      foregroundTaskOptions: ForegroundTaskOptions(
        eventAction: ForegroundTaskEventAction.repeat(60000),
        autoRunOnBoot: true,
        autoRunOnMyPackageReplaced: true,
        allowWakeLock: true,
        allowWifiLock: true, // keep Wi-Fi radio up so the socket survives
      ),
    );
    _initialised = true;
  }

  /// Start holding the connection open. Returns whether the service is running
  /// afterwards. Safe to call repeatedly.
  Future<bool> start({bool wakeWordActive = false}) async {
    try {
      _initOnce();
      if (await FlutterForegroundTask.isRunningService) {
        // Already running — update the notification text if wake word changed.
        await _updateNotification(wakeWordActive: wakeWordActive);
        return true;
      }
      // Battery-optimisation exemption keeps the socket alive on aggressive
      // OEM skins (MIUI in particular kills unexempted services). Asking is
      // best-effort: declining still leaves a working service, just one the
      // system may reclaim sooner.
      if (!await FlutterForegroundTask.isIgnoringBatteryOptimizations) {
        await FlutterForegroundTask.requestIgnoreBatteryOptimization();
      }
      final result = await FlutterForegroundTask.startService(
        serviceId: 512,
        notificationTitle: 'AccessAI is listening',
        notificationText: wakeWordActive
            ? 'Say "Hey Access" to ask a question hands-free.'
            : 'You will be alerted when someone is at the door.',
        callback: _startCallback,
      );
      if (result is ServiceRequestSuccess) return true;
      return await FlutterForegroundTask.isRunningService;
    } catch (_) {
      return false; // unsupported platform / permission refused
    }
  }

  /// Update the persistent notification text to reflect whether wake word is on.
  Future<void> updateWakeWordState({required bool active}) async {
    try {
      if (!await FlutterForegroundTask.isRunningService) return;
      await _updateNotification(wakeWordActive: active);
    } catch (_) {}
  }

  Future<void> _updateNotification({required bool wakeWordActive}) async {
    await FlutterForegroundTask.updateService(
      notificationTitle: 'AccessAI is listening',
      notificationText: wakeWordActive
          ? 'Say "Hey Access" to ask a question hands-free.'
          : 'You will be alerted when someone is at the door.',
    );
  }

  Future<void> stop() async {
    try {
      await FlutterForegroundTask.stopService();
    } catch (_) {}
  }

  /// Register a port so the service worker can send messages to the main
  /// isolate. Call this once in main() before runApp().
  static void initCommunicationPort(void Function(dynamic) onMessage) {
    final port = ReceivePort();
    IsolateNameServer.registerPortWithName(port.sendPort, _portName);
    port.listen(onMessage);
  }
}

/// Entry point for the service isolate. It must be a top-level function.
@pragma('vm:entry-point')
void _startCallback() {
  FlutterForegroundTask.setTaskHandler(_KeepAliveHandler());
}

/// Minimal handler: its only job is to exist so Android keeps the process
/// warm. The real work (WebSocket, notifications, speech) stays in the main
/// isolate -- see the class doc above for why.
///
/// On each repeat event (every 60 s) it pings the main isolate so the wake
/// word watchdog can verify the loop is still alive and restart if needed.
class _KeepAliveHandler extends TaskHandler {
  static const String _portName = 'accessai_bg_port';

  @override
  Future<void> onStart(DateTime timestamp, TaskStarter starter) async {
    // Signal the main isolate that the service started so it can start/verify
    // the wake word loop.
    _ping('started');
  }

  @override
  void onRepeatEvent(DateTime timestamp) {
    // Heartbeat: lets the main isolate know the service is still alive.
    _ping('heartbeat');
  }

  @override
  Future<void> onDestroy(DateTime timestamp, bool isTimeout) async {
    _ping('stopped');
  }

  void _ping(String msg) {
    final send = IsolateNameServer.lookupPortByName(_portName);
    send?.send(msg);
  }
}
