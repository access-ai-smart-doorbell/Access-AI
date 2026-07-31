import 'package:flutter_local_notifications/flutter_local_notifications.dart';

import '../models/visitor_event.dart';

/// LOCAL notifications for doorbell events (no Firebase, nothing leaves the LAN).
///
/// The phone holds the /events WebSocket open itself; when an event arrives and
/// the app is not in the foreground, this posts a system notification so the
/// user is actually alerted. Pairing it with the foreground service in
/// [BackgroundAlertService] is what keeps that socket alive with the screen off.
///
/// Two channels, because Android grades importance per channel and a spoof must
/// be able to break through where a routine "known visitor" should not:
///   * `doorbell`  - MAX importance, full-screen-capable, its own vibration.
///   * `visitor`   - DEFAULT importance for quieter known-person arrivals.
///
/// Every call is best-effort: a denied permission or an unsupported platform
/// degrades to "no notification", never an exception into the event pipeline.
class NotificationService {
  NotificationService();

  static const doorbellChannelId = 'accessai_doorbell';
  static const visitorChannelId = 'accessai_visitor';
  static const serviceChannelId = 'accessai_service';

  final _plugin = FlutterLocalNotificationsPlugin();
  bool _ready = false;

  /// Whether the user granted the POST_NOTIFICATIONS permission (Android 13+).
  /// False also covers "not asked yet"; alerts simply stay silent then.
  bool granted = false;

  Future<void> init() async {
    if (_ready) return;
    const android = AndroidInitializationSettings('@mipmap/ic_launcher');
    await _plugin.initialize(
      const InitializationSettings(android: android),
    );

    final impl = _plugin.resolvePlatformSpecificImplementation<
        AndroidFlutterLocalNotificationsPlugin>();
    if (impl != null) {
      // Android 13+ gates notifications behind a runtime permission. Asking on
      // first launch is right for this app: the entire point is being alerted
      // when you are not looking at the screen.
      granted = await impl.requestNotificationsPermission() ?? false;
      await impl.createNotificationChannel(const AndroidNotificationChannel(
        doorbellChannelId,
        'Doorbell alerts',
        description: 'Someone is at the door.',
        importance: Importance.max,
        playSound: true,
        enableVibration: true,
      ));
      await impl.createNotificationChannel(const AndroidNotificationChannel(
        visitorChannelId,
        'Visitor updates',
        description: 'Recognised visitors and follow-up descriptions.',
        importance: Importance.defaultImportance,
      ));
      await impl.createNotificationChannel(const AndroidNotificationChannel(
        serviceChannelId,
        'Listening for the doorbell',
        description:
            'Keeps the connection to your AccessAI server open in the '
            'background.',
        // LOW so the mandatory foreground-service notice sits silently in the
        // shade instead of buzzing the user every time the app starts.
        importance: Importance.low,
        showBadge: false,
      ));
    }
    _ready = true;
  }

  /// Post the notification for a doorbell event. `title` reads as the headline
  /// ("Someone is at the door"), `body` carries the composed announcement so a
  /// Deaf user gets the full text without unlocking the phone.
  Future<void> showEvent(VisitorEvent ev) async {
    if (!_ready || !granted) return;
    final kind = ev.kind;
    final urgent = kind == 'spoof' || kind == 'unknown' || kind == 'delivery';

    final title = switch (kind) {
      'spoof' => 'Possible spoof at the door',
      'known' => ev.name.trim().isNotEmpty
          ? '${ev.name.trim()} is at the door'
          : 'Someone you know is at the door',
      'delivery' => 'Likely a delivery',
      _ => 'Someone is at the door',
    };
    final body = ev.announcementText.trim().isNotEmpty
        ? ev.announcementText.trim()
        : ev.countLine;

    final details = AndroidNotificationDetails(
      urgent ? doorbellChannelId : visitorChannelId,
      urgent ? 'Doorbell alerts' : 'Visitor updates',
      importance: urgent ? Importance.max : Importance.defaultImportance,
      priority: urgent ? Priority.high : Priority.defaultPriority,
      category: AndroidNotificationCategory.call,
      // The full announcement can be several sentences; BigText lets the user
      // expand and read all of it rather than a truncated line.
      styleInformation: BigTextStyleInformation(body, contentTitle: title),
      ticker: title,
    );

    // Stable id per event so a re-broadcast of the same event (the VLM enrich
    // arrives seconds later) UPDATES the existing notification instead of
    // stacking a second copy.
    await _plugin.show(
      _idFor(ev.eventId),
      title,
      body,
      NotificationDetails(android: details),
      payload: ev.eventId,
    );
  }

  /// A quieter follow-up once the VLM description lands, reusing the event's id
  /// so it replaces rather than duplicates the original alert.
  Future<void> showEnriched(VisitorEvent ev, String description) async {
    if (!_ready || !granted || description.trim().isEmpty) return;
    final details = AndroidNotificationDetails(
      visitorChannelId,
      'Visitor updates',
      importance: Importance.defaultImportance,
      priority: Priority.defaultPriority,
      styleInformation: BigTextStyleInformation(description,
          contentTitle: 'At your door'),
      onlyAlertOnce: true, // already buzzed for this event
    );
    await _plugin.show(_idFor(ev.eventId), 'At your door', description,
        NotificationDetails(android: details), payload: ev.eventId);
  }

  Future<void> cancelAll() async {
    if (!_ready) return;
    await _plugin.cancelAll();
  }

  /// Map an event id string onto a stable 31-bit notification id.
  int _idFor(String eventId) {
    if (eventId.isEmpty) return 1;
    var h = 0;
    for (final c in eventId.codeUnits) {
      h = (h * 31 + c) & 0x3fffffff;
    }
    return h == 0 ? 1 : h;
  }
}
