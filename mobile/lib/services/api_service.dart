import 'dart:typed_data';

import 'package:dio/dio.dart';

import '../core/parse.dart';
import '../models/app_status.dart';
import '../models/known_person.dart';
import '../models/visitor_event.dart';

/// A friendly, already-formatted error the UI can show verbatim. We never let a
/// raw dio stack trace reach the user.
class ApiException implements Exception {
  ApiException(this.message, {this.statusCode});
  final String message;
  final int? statusCode;
  @override
  String toString() => message;
}

/// Thin REST client over the AccessAI backend. Immutable around one base URL —
/// when the URL changes, a fresh ApiService is created (see providers). Every
/// call maps transport/backend failures to an ApiException with a readable
/// message so callers can `try/catch` and show a snackbar instead of crashing.
class ApiService {
  ApiService(this.baseUrl, {this.token = '', this.deviceId = ''})
      : _dio = Dio(BaseOptions(
          baseUrl: baseUrl,
          connectTimeout: const Duration(seconds: 6),
          receiveTimeout: const Duration(seconds: 30),
          sendTimeout: const Duration(seconds: 30),
          // Phase 17: bearer token when the server sets ACCESSAI_TOKEN.
          headers: token.isEmpty
              ? null
              : {'Authorization': 'Bearer $token'},
          // Accept any status; we branch on it ourselves for clean messages.
          validateStatus: (_) => true,
        ));

  final String baseUrl;
  final String token;

  /// Opaque per-install id sent as `device` on /mode so this phone can hold its
  /// own accessibility mode independent of the household default (Phase 17).
  /// Empty => behaves exactly as before (touches the household default).
  final String deviceId;
  final Dio _dio;

  /// Append ?token= for consumers that can't send headers (MJPEG <img>,
  /// snapshots, audio URLs, the WebSocket).
  String _tq(String url) {
    if (token.isEmpty) return url;
    final sep = url.contains('?') ? '&' : '?';
    return '$url${sep}token=${Uri.encodeQueryComponent(token)}';
  }

  // --- URL helpers (for MJPEG / <img>) -----------------------------------
  String get videoUrl => _tq('$baseUrl/video');
  String knownPhotoUrl(String name) =>
      _tq('$baseUrl/known_photo/${Uri.encodeComponent(name)}');
  String snapshotUrl(String eventId) =>
      _tq('$baseUrl/snapshot/${Uri.encodeComponent(eventId)}');
  String speakAudioUrl(String text, [String lang = '']) =>
      _tq('$baseUrl/speak_audio?text=${Uri.encodeQueryComponent(text)}${lang.isNotEmpty ? '&lang=${Uri.encodeQueryComponent(lang)}' : ''}');

  Uri get eventsWsUri {
    final u = Uri.parse(baseUrl);
    final scheme = u.scheme == 'https' ? 'wss' : 'ws';
    return u.replace(scheme: scheme, path: '/events', queryParameters: {
      if (token.isNotEmpty) 'token': token,
    });
  }

  // --- Core requests ------------------------------------------------------
  Never _fail(Object e) {
    if (e is DioException) {
      if (e.type == DioExceptionType.connectionTimeout ||
          e.type == DioExceptionType.connectionError) {
        throw ApiException(
            'Can’t reach the server at $baseUrl. Check it’s running and on the '
            'same Wi‑Fi, then test the connection in Settings.');
      }
      throw ApiException('Network error: ${e.message ?? e.type.name}');
    }
    throw ApiException('Unexpected error: $e');
  }

  Map<String, dynamic> _okMap(Response r) {
    if (r.statusCode == 401) {
      throw ApiException(
          'The server requires an access token. Enter it in Settings.',
          statusCode: 401);
    }
    if (r.statusCode == null || r.statusCode! >= 400) {
      final detail = r.data is Map ? asStr((r.data as Map)['detail']) : '';
      throw ApiException(
          detail.isNotEmpty ? detail : 'Server returned ${r.statusCode}',
          statusCode: r.statusCode);
    }
    return asMap(r.data);
  }

  Future<Map<String, dynamic>> _get(String path,
      {Map<String, dynamic>? query}) async {
    try {
      return _okMap(await _dio.get(path, queryParameters: query));
    } catch (e) {
      _fail(e);
    }
  }

  Future<Map<String, dynamic>> _post(String path, {Object? body}) async {
    try {
      return _okMap(await _dio.post(path, data: body));
    } catch (e) {
      _fail(e);
    }
  }

  // --- Health / connection -----------------------------------------------
  Future<AppStatus> status() async => AppStatus.fromJson(await _get('/status'));

  /// A quick reachability probe used by the Settings "Test connection" button.
  Future<AppStatus> testConnection() => status();

  // --- Doorbell + door actions -------------------------------------------
  /// Ring: visual-only, fast, records NOTHING. Returns the new event.
  Future<VisitorEvent> trigger() async =>
      VisitorEvent.fromJson(await _post('/trigger'));

  /// The ONLY recorder. Captures the visitor for a few seconds, transcribes +
  /// translates. Returns {transcript, translated, language, event_id}.
  Future<Map<String, dynamic>> hearVisitor() => _post('/hear_visitor');

  /// Speak a typed reply at the door.
  Future<Map<String, dynamic>> reply(String text) =>
      _post('/reply', body: {'text': text});

  /// Text/voice command; returns {intent, answer, text}.
  Future<Map<String, dynamic>> command(String text) =>
      _post('/command', body: {'text': text});

  /// Free-form visual question about the current scene ("what colour is their
  /// dress", "what is he doing now"). Returns {question, answer}. `speak` asks
  /// the backend to also voice the answer on the doorbell's speaker.
  /// Throws ApiException (503) when the VLM isn't available so the UI can say so.
  Future<String> ask(String question, {bool speak = false}) async {
    final m = await _post('/ask', body: {'question': question, 'speak': speak});
    return asStr(m['answer']);
  }

  // --- Translation language ----------------------------------------------
  /// Current translation backend + target language for the picker/pill.
  /// Returns {enabled, available, backend, user_language, user_language_name}.
  Future<Map<String, dynamic>> translateStatus() => _get('/translate_status');

  /// Change the user's target language live (and persist it server-side).
  /// Returns the sanitized {user_language, user_language_name}.
  Future<Map<String, dynamic>> setUserLanguage(String code) =>
      _post('/user_language', body: {'lang': code});

  // --- History ------------------------------------------------------------
  Future<List<VisitorEvent>> history({int limit = 50, String q = ''}) async {
    try {
      final r = await _dio.get('/history', queryParameters: {
        'limit': limit,
        if (q.trim().isNotEmpty) 'q': q.trim(),
      });
      if (r.statusCode != 200) {
        throw ApiException('Could not load history (${r.statusCode}).');
      }
      final data = r.data;
      final list = data is List ? data : const [];
      return list
          .whereType<Map>()
          .map((e) => VisitorEvent.fromJson(e.cast<String, dynamic>()))
          .toList();
    } catch (e) {
      _fail(e);
    }
  }

  Future<VisitorEvent> event(String id) async => VisitorEvent.fromJson(
      await _get('/event/${Uri.encodeComponent(id)}'));

  Future<void> deleteEvent(String id) =>
      _post('/event/${Uri.encodeComponent(id)}/delete');

  Future<int> clearHistory() async {
    final m = await _post('/history/clear');
    return asInt(m['cleared']);
  }

  // --- Known people -------------------------------------------------------
  Future<List<KnownPerson>> known() async {
    final m = await _get('/known');
    return asMapList(m['people']).map(KnownPerson.fromJson).toList();
  }

  Future<void> deleteKnown(String name) =>
      _post('/known/delete', body: {'name': name});

  /// Enrol a person from one or more photo files (multipart). `photos` is a list
  /// of (filename, bytes).
  Future<Map<String, dynamic>> enrollUpload(
      String name, List<({String filename, Uint8List bytes})> photos) async {
    try {
      final form = FormData();
      form.fields.add(MapEntry('name', name));
      for (final p in photos) {
        // The backend decodes each upload with cv2.imdecode on the raw bytes,
        // so the MIME type is irrelevant — filename alone is enough.
        form.files.add(MapEntry(
          'files',
          MultipartFile.fromBytes(p.bytes, filename: p.filename),
        ));
      }
      final r = await _dio.post('/enroll_upload', data: form);
      return _okMap(r);
    } catch (e) {
      if (e is ApiException) rethrow;
      _fail(e);
    }
  }

  // --- Mode ---------------------------------------------------------------
  // When [deviceId] is set, /mode carries it so this phone gets its OWN mode
  // (Phase 17 per-device modes) instead of moving the household default. The
  // server echoes the mode that actually applies to this device.
  Future<String> getMode() async {
    final m = await _get('/mode',
        query: deviceId.isEmpty ? null : {'device': deviceId});
    return asStr(m['mode'], 'both');
  }

  Future<String> setMode(String mode) async {
    final body = <String, dynamic>{'mode': mode};
    if (deviceId.isNotEmpty) body['device'] = deviceId;
    return asStr((await _post('/mode', body: body))['mode'], mode);
  }

  /// Drop this device's override so it follows the household default again.
  /// No-op on servers without per-device support; returns the applied mode.
  Future<String> clearDeviceMode() async {
    if (deviceId.isEmpty) return getMode();
    try {
      final r = _okMap(await _dio.delete('/mode',
          queryParameters: {'device': deviceId}));
      return asStr(r['mode'], 'both');
    } catch (e) {
      if (e is ApiException) rethrow;
      _fail(e);
    }
  }

  /// Canned one-tap replies from the server (Phase 17). Falls back to a small
  /// built-in set if the server is older or the call fails, so the UI is never
  /// empty.
  Future<List<String>> quickReplies() async {
    try {
      final m = await _get('/quick_replies');
      final list = (m['replies'] as List?)
              ?.map((e) => e.toString())
              .where((s) => s.trim().isNotEmpty)
              .toList() ??
          const <String>[];
      if (list.isNotEmpty) return list;
    } catch (_) {
      // fall through to defaults
    }
    return const [
      "I'll be right there",
      'Please leave it at the door',
      "Sorry, I'm not available",
      'Who is it?',
    ];
  }

  // --- Voices -------------------------------------------------------------
  Future<({List<VoiceOption> voices, String current})> voices() async {
    final m = await _get('/voices');
    final list = asMapList(m['voices']).map(VoiceOption.fromJson).toList();
    return (voices: list, current: asStr(m['current']));
  }

  Future<Map<String, dynamic>> setVoice(String id) =>
      _post('/voice', body: {'id': id});

  // --- Blind-mode speech: fetch synthesized WAV bytes --------------------
  /// Returns WAV bytes for the phone to play, or throws ApiException (e.g. 503)
  /// so the caller falls back to on-device TTS.
  Future<Uint8List> speakAudio(String text) async {
    try {
      final r = await _dio.get<List<int>>(
        '/speak_audio',
        queryParameters: {'text': text},
        options: Options(responseType: ResponseType.bytes),
      );
      if (r.statusCode != 200 || r.data == null || r.data!.isEmpty) {
        throw ApiException('No server audio (${r.statusCode}).',
            statusCode: r.statusCode);
      }
      return Uint8List.fromList(r.data!);
    } catch (e) {
      if (e is ApiException) rethrow;
      _fail(e);
    }
  }

  // --- Video clips ----------------------------------------------------------
  /// Fetches the list of recorded video clips from the server.
  /// Returns a list of clip metadata maps with keys: filename, size_bytes,
  /// modified, event_id, etc.
  Future<List<Map<String, dynamic>>> clipsList() async {
    final m = await _get('/clips');
    final raw = m['clips'];
    if (raw is List) {
      return raw
          .whereType<Map>()
          .map((e) => e.cast<String, dynamic>())
          .toList();
    }
    return const [];
  }

  /// Returns the full URL for streaming/downloading a specific clip.
  String clipUrl(String filename) =>
      _tq('$baseUrl/clips/${Uri.encodeComponent(filename)}');

  /// Returns the full URL for the clip associated with a specific event.
  String eventClipUrl(String eventId) =>
      _tq('$baseUrl/event/${Uri.encodeComponent(eventId)}/clip');

  /// Deletes a video clip by filename.
  Future<void> deleteClip(String filename) async {
    try {
      final r = await _dio.delete(
          '/clips/${Uri.encodeComponent(filename)}');
      if (r.statusCode == null || r.statusCode! >= 400) {
        final detail = r.data is Map
            ? asStr((r.data as Map)['detail'])
            : '';
        throw ApiException(
            detail.isNotEmpty ? detail : 'Delete failed (${r.statusCode})',
            statusCode: r.statusCode);
      }
    } catch (e) {
      if (e is ApiException) rethrow;
      _fail(e);
    }
  }

  void close() => _dio.close(force: true);
}
