import 'dart:math';

import 'package:shared_preferences/shared_preferences.dart';

import '../core/theme.dart';

/// Persistent settings: the server base URL (the one thing the app can't guess),
/// accessibility mode, and theme choice. Backed by shared_preferences. Reads are
/// null-safe with sensible LAN defaults for first run.
class PrefsService {
  PrefsService(this._prefs);

  final SharedPreferences _prefs;

  static const _kBaseUrl = 'base_url';
  static const _kMode = 'mode';
  static const _kTheme = 'theme_choice';
  static const _kConfigured = 'configured';
  static const _kWakeWord = 'wakeword_enabled';
  static const _kToken = 'auth_token';
  static const _kDeviceId = 'device_id';

  /// First-run default — a common LAN guess. The user confirms/edits it in the
  /// Settings first-run panel.
  static const String defaultBaseUrl = 'http://192.168.1.78:8000';

  String get baseUrl {
    final v = (_prefs.getString(_kBaseUrl) ?? '').trim();
    return v.isEmpty ? defaultBaseUrl : _normalize(v);
  }

  Future<void> setBaseUrl(String url) async {
    await _prefs.setString(_kBaseUrl, _normalize(url));
    await _prefs.setBool(_kConfigured, true);
  }

  /// True once the user has explicitly saved a server URL at least once.
  bool get isConfigured => _prefs.getBool(_kConfigured) ?? false;

  /// Bearer token matching the server's ACCESSAI_TOKEN (Phase 17). Empty when
  /// the server runs open (no auth). Sent on every request when set.
  String get authToken => (_prefs.getString(_kToken) ?? '').trim();
  Future<void> setAuthToken(String token) =>
      _prefs.setString(_kToken, token.trim());

  String get mode => _prefs.getString(_kMode) ?? 'both';
  Future<void> setMode(String mode) => _prefs.setString(_kMode, mode);

  /// A stable per-install id sent as `device` on /mode so this phone can carry
  /// its OWN accessibility mode (e.g. Deaf) while another device stays on the
  /// household default (Phase 17 per-device modes). Generated once, then
  /// persisted; opaque to the server. No extra dependency — derived from time
  /// plus a random suffix, which is unique enough for a handful of home devices.
  String get deviceId {
    var id = (_prefs.getString(_kDeviceId) ?? '').trim();
    if (id.isEmpty) {
      final rnd = Random();
      final suffix = List.generate(6,
          (_) => rnd.nextInt(36).toRadixString(36)).join();
      id = 'dev-${DateTime.now().microsecondsSinceEpoch.toRadixString(36)}'
          '-$suffix';
      _prefs.setString(_kDeviceId, id); // fire-and-forget persist
    }
    return id;
  }

  /// On-device "hey access" always-on wake word. OFF by default: an open mic is
  /// a battery + privacy choice the user opts into. Persisted so the background
  /// listener resumes on next launch.
  bool get wakeWordEnabled => _prefs.getBool(_kWakeWord) ?? false;
  Future<void> setWakeWordEnabled(bool on) =>
      _prefs.setBool(_kWakeWord, on);

  AppThemeChoice get themeChoice =>
      AppThemeChoiceLabel.fromId(_prefs.getString(_kTheme));
  Future<void> setThemeChoice(AppThemeChoice c) =>
      _prefs.setString(_kTheme, c.id);

  /// Public form of [_normalize] for callers that need to normalise a URL
  /// before persisting it (e.g. the first-run connection test).
  static String normalizeUrl(String raw) => _normalize(raw);

  /// Trim, strip a trailing slash, and prepend http:// if the user typed a bare
  /// host:port. Keeps the URL WebSocket/REST-ready.
  static String _normalize(String raw) {
    var s = raw.trim();
    if (s.isEmpty) return s;
    if (!s.startsWith('http://') && !s.startsWith('https://')) {
      s = 'http://$s';
    }
    while (s.endsWith('/')) {
      s = s.substring(0, s.length - 1);
    }
    return s;
  }
}
