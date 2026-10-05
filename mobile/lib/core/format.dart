/// Presentation helpers that enforce the project's conservative-language rules:
/// age is ONLY ever an approximate decade band, expressions are hedged, and
/// nothing is stated with false certainty.
library;

/// Age → a hedged decade band. Never a raw number. Returns '' if unknown.
String ageBand(int? age) {
  if (age == null || age <= 0) return '';
  if (age < 13) return 'likely a child';
  final band = (age ~/ 10) * 10;
  const names = {
    10: 'teens',
    20: 'twenties',
    30: 'thirties',
    40: 'forties',
    50: 'fifties',
    60: 'sixties',
    70: 'seventies',
  };
  final n = names[band] ?? 'eighties or older';
  return 'likely in their $n';
}

/// Hedge an expression cue: "calm" → "appears calm". Returns '' if empty.
String hedgedExpression(String expr) {
  final e = expr.trim();
  if (e.isEmpty) return '';
  final low = e.toLowerCase();
  if (low.startsWith('appears') || low.startsWith('looks') ||
      low.startsWith('seems')) {
    return e;
  }
  return 'appears $low';
}

/// Parse the backend ISO-ish timestamp and format a short, friendly label.
/// Falls back to the raw string if it can't be parsed.
String prettyTime(String iso) {
  final dt = DateTime.tryParse(iso);
  if (dt == null) return iso;
  final local = dt.toLocal();
  final now = DateTime.now();
  final sameDay = local.year == now.year &&
      local.month == now.month &&
      local.day == now.day;
  final hh = local.hour % 12 == 0 ? 12 : local.hour % 12;
  final mm = local.minute.toString().padLeft(2, '0');
  final ap = local.hour < 12 ? 'AM' : 'PM';
  final time = '$hh:$mm $ap';
  if (sameDay) return 'Today • $time';
  final yday = now.subtract(const Duration(days: 1));
  final isYday = local.year == yday.year &&
      local.month == yday.month &&
      local.day == yday.day;
  if (isYday) return 'Yesterday • $time';
  return '${local.day}/${local.month} $time';
}

String modeLabel(String mode) => switch (mode) {
      'blind' => 'Blind — spoken',
      'deaf' => 'Deaf — visual + vibration',
      _ => 'Both — spoken + visual',
    };

String modeShort(String mode) => switch (mode) {
      'blind' => 'Blind',
      'deaf' => 'Deaf',
      _ => 'Both',
    };
