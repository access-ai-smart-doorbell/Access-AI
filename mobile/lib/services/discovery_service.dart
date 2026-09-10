import 'dart:async';
import 'dart:io';

import 'package:dio/dio.dart';

/// Scans the local LAN subnet for a running AccessAI server.
///
/// Strategy: get the device's own Wi-Fi IP, derive the /24 subnet, then fire
/// 254 concurrent HTTP GET /status probes with a short timeout. Returns the
/// first URL that responds with the AccessAI signature, or null if none found.
///
/// Uses only Dio (already a dependency) — no extra packages needed.
class DiscoveryService {
  static const int _port = 8000;
  static const Duration _probeTimeout = Duration(milliseconds: 800);

  /// Scan the LAN for an AccessAI server.
  ///
  /// [onProgress] is called with values 0.0–1.0 so the UI can show a progress
  /// bar. [token] is included on the /status probe when set so auth-protected
  /// servers don't return 401.
  ///
  /// Returns the base URL (e.g. `http://192.168.1.78:8000`) or null.
  static Future<String?> scan({
    void Function(double progress)? onProgress,
    String token = '',
  }) async {
    // 1. Find our own LAN IP to derive the subnet prefix.
    final myIp = await _localIp();
    if (myIp == null) return null;

    final parts = myIp.split('.');
    if (parts.length != 4) return null;
    final prefix = '${parts[0]}.${parts[1]}.${parts[2]}';

    // 2. Probe all 254 hosts concurrently.
    final completer = Completer<String?>();
    int completed = 0;
    const total = 254;

    final futures = <Future<void>>[];
    for (int i = 1; i <= total; i++) {
      final ip = '$prefix.$i';
      futures.add(_probe(ip, token).then((found) {
        completed++;
        onProgress?.call(completed / total);
        if (found && !completer.isCompleted) {
          completer.complete('http://$ip:$_port');
        }
      }));
    }

    // 3. When all probes finish without a hit, complete with null.
    Future.wait(futures).then((_) {
      if (!completer.isCompleted) completer.complete(null);
    });

    return completer.future;
  }

  /// Probe a single IP: returns true if it looks like an AccessAI server.
  static Future<bool> _probe(String ip, String token) async {
    final dio = Dio(BaseOptions(
      connectTimeout: _probeTimeout,
      receiveTimeout: _probeTimeout,
      validateStatus: (_) => true,
    ));
    try {
      final url = 'http://$ip:$_port/status';
      final resp = await dio.get<Map<String, dynamic>>(
        url,
        options: Options(
          headers: token.isNotEmpty
              ? {'Authorization': 'Bearer $token'}
              : null,
          responseType: ResponseType.json,
        ),
      );
      // Accept 200 or 401 (auth required but server IS AccessAI).
      if (resp.statusCode != 200 && resp.statusCode != 401) return false;
      if (resp.statusCode == 401) return true; // auth-protected = definitely it

      // Check for the AccessAI signature in the response body.
      final body = resp.data;
      if (body is Map<String, dynamic>) {
        return body.containsKey('mode') ||
            body.containsKey('doorbell') ||
            body.containsKey('version');
      }
      return false;
    } catch (_) {
      return false;
    } finally {
      dio.close(force: true);
    }
  }

  /// Get the device's own LAN IP address (first non-loopback IPv4 address).
  static Future<String?> _localIp() async {
    try {
      final interfaces = await NetworkInterface.list(
        type: InternetAddressType.IPv4,
        includeLoopback: false,
      );
      for (final iface in interfaces) {
        for (final addr in iface.addresses) {
          final ip = addr.address;
          // Skip link-local and loopback.
          if (ip.startsWith('127.') || ip.startsWith('169.254.')) continue;
          // Prefer common LAN ranges.
          if (ip.startsWith('192.168.') ||
              ip.startsWith('10.') ||
              ip.startsWith('172.')) {
            return ip;
          }
        }
      }
      // Fall back to any non-loopback IPv4.
      for (final iface in interfaces) {
        for (final addr in iface.addresses) {
          if (!addr.isLoopback) return addr.address;
        }
      }
    } catch (_) {}
    return null;
  }
}
