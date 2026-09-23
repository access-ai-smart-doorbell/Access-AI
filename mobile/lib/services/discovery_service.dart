import 'dart:async';
import 'dart:io';

import 'package:dio/dio.dart';

/// Scans the local LAN subnet for a running AccessAI server.
///
/// Strategy:
///   1. Get the device's own Wi-Fi IP → derive /24 subnet.
///   2. Probe the SERVER's known IP first (saved in prefs) for instant reconnect.
///   3. Fire 254 concurrent HTTP GET /status probes with a short timeout.
///   4. Returns the first URL that responds with the AccessAI signature.
///
/// Auth: AccessAI uses ?token= query param (not Authorization header).
class DiscoveryService {
  static const int _port = 8000;
  static const Duration _probeTimeout = Duration(milliseconds: 1200);

  /// Scan the LAN for an AccessAI server.
  ///
  /// [onProgress] is called with values 0.0–1.0 for a progress bar.
  /// [token] is appended as ?token= on the /status probe.
  /// [knownIp] is tried first (instant hit if server didn't move).
  ///
  /// Returns the base URL (e.g. `http://192.168.1.78:8000`) or null.
  static Future<String?> scan({
    void Function(double progress)? onProgress,
    String token = '',
    String? knownIp,
  }) async {
    // 1. Find our own LAN IP to derive the subnet prefix.
    final myIp = await _localIp();
    if (myIp == null) return null;

    final parts = myIp.split('.');
    if (parts.length != 4) return null;
    final prefix = '${parts[0]}.${parts[1]}.${parts[2]}';

    // 2. Try the previously known IP first — instant reconnect.
    if (knownIp != null && knownIp.isNotEmpty) {
      final knownHost = knownIp
          .replaceFirst(RegExp(r'^https?://'), '')
          .split(':')[0];
      if (await _probe(knownHost, token)) {
        return 'http://$knownHost:$_port';
      }
    }

    // 3. Also try the server IP most likely to be the laptop
    //    (same subnet, .1, .2, .100, .101 are common router/laptop IPs).
    final priorityHosts = <String>[];
    for (final suffix in [1, 2, 100, 101, 105, 110, 150, 200]) {
      final ip = '$prefix.$suffix';
      if (ip != myIp) priorityHosts.add(ip);
    }
    for (final ip in priorityHosts) {
      if (await _probe(ip, token)) return 'http://$ip:$_port';
    }

    // 4. Full subnet scan — all 254 hosts concurrently.
    final completer = Completer<String?>();
    int completed = 0;
    const total = 254;

    final futures = <Future<void>>[];
    for (int i = 1; i <= total; i++) {
      final ip = '$prefix.$i';
      if (ip == myIp) {
        // Skip our own IP.
        completed++;
        onProgress?.call(completed / total);
        continue;
      }
      futures.add(_probe(ip, token).then((found) {
        completed++;
        onProgress?.call(completed / total);
        if (found && !completer.isCompleted) {
          completer.complete('http://$ip:$_port');
        }
      }));
    }

    // When all probes finish without a hit, complete with null.
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
      // AccessAI uses ?token= query param, NOT Authorization header.
      final url = token.isNotEmpty
          ? 'http://$ip:$_port/status?token=${Uri.encodeComponent(token)}'
          : 'http://$ip:$_port/status';

      final resp = await dio.get<dynamic>(
        url,
        options: Options(responseType: ResponseType.json),
      );

      // 200 with AccessAI signature = found.
      // 401 = auth required but server IS alive = found.
      // 403 = token wrong but server IS alive = found.
      if (resp.statusCode == 401 || resp.statusCode == 403) return true;
      if (resp.statusCode != 200) return false;

      final body = resp.data;
      if (body is Map<String, dynamic>) {
        // Look for the AccessAI-specific keys in /status response.
        return body.containsKey('mode') ||
            body.containsKey('app') ||
            body.containsKey('doorbell') ||
            body.containsKey('version') ||
            body['app'] == 'AccessAI';
      }
      return false;
    } catch (_) {
      return false;
    } finally {
      dio.close(force: true);
    }
  }

  /// Get the device's own LAN IP address (Wi-Fi preferred).
  static Future<String?> _localIp() async {
    try {
      final interfaces = await NetworkInterface.list(
        type: InternetAddressType.IPv4,
        includeLoopback: false,
      );

      // Prefer wlan/wifi interfaces (Android: wlan0, iOS: en0).
      for (final iface in interfaces) {
        final name = iface.name.toLowerCase();
        if (!name.contains('wlan') &&
            !name.contains('wifi') &&
            !name.contains('en0') &&
            !name.contains('wlp')) continue;
        for (final addr in iface.addresses) {
          final ip = addr.address;
          if (ip.startsWith('127.') || ip.startsWith('169.254.')) continue;
          if (ip.startsWith('192.168.') ||
              ip.startsWith('10.') ||
              ip.startsWith('172.')) return ip;
        }
      }

      // Fallback: any non-loopback LAN IPv4.
      for (final iface in interfaces) {
        for (final addr in iface.addresses) {
          final ip = addr.address;
          if (ip.startsWith('127.') || ip.startsWith('169.254.')) continue;
          if (ip.startsWith('192.168.') ||
              ip.startsWith('10.') ||
              ip.startsWith('172.')) return ip;
        }
      }

      // Last resort: anything non-loopback.
      for (final iface in interfaces) {
        for (final addr in iface.addresses) {
          if (!addr.isLoopback) return addr.address;
        }
      }
    } catch (_) {}
    return null;
  }

  /// Quick check: is the server at [baseUrl] still alive?
  /// Used by the settings screen to re-verify a saved URL.
  static Future<bool> verify(String baseUrl, {String token = ''}) async {
    final host = baseUrl
        .replaceFirst(RegExp(r'^https?://'), '')
        .split(':')[0];
    return _probe(host, token);
  }
}
