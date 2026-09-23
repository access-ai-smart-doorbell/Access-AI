import '../core/parse.dart';

/// One detected person at the door. Mirrors the backend `Person` dataclass.
/// KNOWN people are named only (no age/gender surfaced); UNKNOWN people carry a
/// cautious, approximate description.
class Person {
  final bool known;
  final String name;
  final double confidence;
  final int? age; // approximate; ONLY shown as a range, and never for known
  final String gender; // "man" | "woman" | ""
  final bool isSpoof;
  final double spoofScore; // 1.0 real -> 0.0 spoof
  final String appearance; // clothing / carried (unknown only)
  final String expression; // cautious mood cue (unknown only)
  final String clothing;
  final String action;
  final String position;

  const Person({
    required this.known,
    required this.name,
    required this.confidence,
    required this.age,
    required this.gender,
    required this.isSpoof,
    required this.spoofScore,
    required this.appearance,
    required this.expression,
    required this.clothing,
    required this.action,
    required this.position,
  });

  factory Person.fromJson(Map<String, dynamic> j) => Person(
        known: asBool(j['known']),
        name: asStr(j['name'], 'Unknown'),
        confidence: asDouble(j['confidence']),
        age: asIntOrNull(j['age']),
        gender: asStr(j['gender']),
        isSpoof: asBool(j['is_spoof']),
        spoofScore: asDouble(j['spoof_score'], 1.0),
        appearance: asStr(j['appearance']),
        expression: asStr(j['expression']),
        clothing: asStr(j['clothing']),
        action: asStr(j['action']),
        position: asStr(j['position']),
      );
}

/// A visitor event — the "spine" object. Tolerant of missing fields so older or
/// partial rows never break the UI.
class VisitorEvent {
  final String eventId;
  final String timestamp;
  final String trigger;

  // Primary identity (mirrors the largest/first-known person).
  final bool known;
  final String name;
  final double identityConfidence;

  final bool isSpoof;
  final double spoofScore;

  // Multi-person.
  final List<Person> people;
  final int extraUnknown;
  final int visitorCount;

  // Vision / scene.
  final List<String> carriedObjects;
  final String sceneSummary;
  final String hazards;
  final String ocrText;

  // Description (unknown-only, hedged).
  final int? age;
  final String gender;
  final String appearance;

  // Speech.
  final String speechTranscript;
  final String languageDetected;
  final String translatedTranscript;

  // Re-ID.
  final String? reidId;
  final int reidSeenCount;

  // Context.
  final String intent;
  final String announcementText;

  /// Server-derived alert taxonomy (Phase 17): spoof | known | delivery |
  /// unknown. Keys the vibration pattern / earcon / notification urgency.
  final String alertKind;

  const VisitorEvent({
    required this.eventId,
    required this.timestamp,
    required this.trigger,
    required this.known,
    required this.name,
    required this.identityConfidence,
    required this.isSpoof,
    required this.spoofScore,
    required this.people,
    required this.extraUnknown,
    required this.visitorCount,
    required this.carriedObjects,
    required this.sceneSummary,
    required this.hazards,
    required this.ocrText,
    required this.age,
    required this.gender,
    required this.appearance,
    required this.speechTranscript,
    required this.languageDetected,
    required this.translatedTranscript,
    required this.reidId,
    required this.reidSeenCount,
    required this.intent,
    required this.announcementText,
    this.alertKind = '',
  });

  factory VisitorEvent.fromJson(Map<String, dynamic> j) {
    final identity = asMap(j['identity']);
    final people = asMapList(j['people']).map(Person.fromJson).toList();
    return VisitorEvent(
      eventId: asStr(j['event_id']),
      timestamp: asStr(j['timestamp']),
      trigger: asStr(j['trigger'], 'manual'),
      known: asBool(identity['known']),
      name: asStr(identity['name'], 'Unknown'),
      identityConfidence: asDouble(identity['confidence']),
      isSpoof: asBool(j['is_spoof']),
      spoofScore: asDouble(j['spoof_score'], 1.0),
      people: people,
      extraUnknown: asInt(j['extra_unknown']),
      visitorCount: asInt(j['visitor_count']),
      carriedObjects: asStrList(j['carried_objects']),
      sceneSummary: asStr(j['scene_summary']),
      hazards: asStr(j['hazards'], 'none'),
      ocrText: asStr(j['ocr_text']),
      age: asIntOrNull(j['age']),
      gender: asStr(j['gender']),
      appearance: asStr(j['appearance']),
      speechTranscript: asStr(j['speech_transcript']),
      languageDetected: asStr(j['language_detected']),
      translatedTranscript: asStr(j['translated_transcript']),
      reidId: j['reid_id'] == null ? null : asStr(j['reid_id']),
      reidSeenCount: asInt(j['reid_seen_count']),
      intent: asStr(j['intent'], 'unknown visitor'),
      announcementText: asStr(j['announcement_text']),
      alertKind: asStr(j['alert_kind']),
    );
  }

  /// The alert kind, deriving locally when the server didn't send one (older
  /// backend): spoof beats known beats delivery beats unknown.
  String get kind {
    if (alertKind.isNotEmpty) return alertKind;
    if (isSpoof || anySpoof) return 'spoof';
    if (known || people.any((p) => p.known)) return 'known';
    if (intent.contains('delivery')) return 'delivery';
    return 'unknown';
  }

  /// How many people the UI should present. Prefer visitor_count; fall back to
  /// people + extras so a partial event still reads sensibly.
  int get totalPeople {
    final byParts = people.length + extraUnknown;
    return visitorCount > 0 ? visitorCount : (byParts > 0 ? byParts : 0);
  }

  int get knownCount => people.where((p) => p.known && !p.isSpoof).length;

  int get unknownCount {
    final total = totalPeople;
    final k = knownCount;
    final u = total - k;
    return u < 0 ? 0 : u;
  }

  bool get anySpoof => isSpoof || people.any((p) => p.isSpoof);

  /// "3 people — 1 known, 2 unknown" (grammatical for 0/1/many).
  String get countLine {
    final total = totalPeople;
    if (total <= 0) return 'No one detected';
    final noun = total == 1 ? 'person' : 'people';
    final parts = <String>[];
    if (knownCount > 0) parts.add('$knownCount known');
    if (unknownCount > 0) parts.add('$unknownCount unknown');
    if (parts.isEmpty) return '$total $noun';
    return '$total $noun — ${parts.join(', ')}';
  }

  bool get hasSpeech =>
      speechTranscript.trim().isNotEmpty ||
      translatedTranscript.trim().isNotEmpty;
}
