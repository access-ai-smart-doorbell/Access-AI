import 'package:flutter/material.dart';
import 'package:flutter_animate/flutter_animate.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../core/glass.dart';
import '../core/motion.dart';
import '../core/tokens.dart';
import '../core/ui.dart';
import '../core/theme.dart';
import '../models/app_status.dart';
import '../services/api_service.dart';
import '../services/prefs_service.dart';
import '../state/providers.dart';
import '../widgets/status_pill.dart';

/// Settings — connectivity, accessibility mode, voice, theme, and a live health
/// panel. The server URL is the one thing the app can't infer; everything else
/// has a safe default. Includes an accessibility read-out (text scale, reduce
/// motion) so the user can confirm the app is honouring their system settings.
class SettingsScreen extends ConsumerStatefulWidget {
  const SettingsScreen({super.key});

  @override
  ConsumerState<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends ConsumerState<SettingsScreen> {
  late final TextEditingController _url;
  late final TextEditingController _token;
  bool _testing = false;
  AppStatus? _health;
  String? _healthError;

  List<VoiceOption> _voices = const [];
  String _currentVoice = '';
  bool _voicesLoading = false;

  // Translation language (POST /user_language, GET /translate_status).
  String _langCode = '';
  bool _langLoading = false;
  bool _translateAvailable = false;

  // Target languages the backend TranslateModule understands (must match its
  // _DEFAULT_LANGUAGE_NAMES). Malayalam is the project default.
  static const List<({String code, String name})> _languages = [
    (code: 'en', name: 'English'),
    (code: 'ml', name: 'Malayalam'),
    (code: 'hi', name: 'Hindi'),
    (code: 'ta', name: 'Tamil'),
    (code: 'te', name: 'Telugu'),
    (code: 'kn', name: 'Kannada'),
    (code: 'bn', name: 'Bengali'),
    (code: 'mr', name: 'Marathi'),
    (code: 'gu', name: 'Gujarati'),
    (code: 'pa', name: 'Punjabi'),
    (code: 'ur', name: 'Urdu'),
  ];

  @override
  void initState() {
    super.initState();
    _url = TextEditingController(text: ref.read(baseUrlProvider));
    _token = TextEditingController(text: ref.read(authTokenProvider));
    _loadVoices();
    _loadLanguage();
  }

  @override
  void dispose() {
    _url.dispose();
    _token.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    final v = _url.text.trim();
    if (v.isEmpty) {
      showSnack(context, 'Enter the server address', error: true);
      return;
    }
    await ref.read(baseUrlProvider.notifier).set(v);
    await ref.read(authTokenProvider.notifier).set(_token.text);
    _url.text = ref.read(baseUrlProvider); // reflect normalized form
    ref.invalidate(historyProvider);
    ref.invalidate(knownProvider);
    if (mounted) showSnack(context, 'Saved. Reconnecting…');
    _loadVoices();
  }

  Future<void> _test() async {
    // Persist first so we test what the user typed.
    await _save();
    setState(() {
      _testing = true;
      _health = null;
      _healthError = null;
    });
    try {
      final s = await ref.read(apiProvider).testConnection();
      setState(() => _health = s);
      // Adopt the server's current mode so the two stay in sync.
      ref.read(modeProvider.notifier).adopt(s.mode);
    } on ApiException catch (e) {
      setState(() => _healthError = e.message);
    } finally {
      if (mounted) setState(() => _testing = false);
    }
  }

  Future<void> _loadVoices() async {
    setState(() => _voicesLoading = true);
    try {
      final r = await ref.read(apiProvider).voices();
      setState(() {
        _voices = r.voices;
        _currentVoice = r.current;
      });
    } catch (_) {
      setState(() => _voices = const []);
    } finally {
      if (mounted) setState(() => _voicesLoading = false);
    }
  }

  Future<void> _setVoice(String id) async {
    setState(() => _currentVoice = id);
    try {
      await ref.read(apiProvider).setVoice(id);
      if (mounted) showSnack(context, 'Voice updated');
    } on ApiException catch (e) {
      if (mounted) showSnack(context, e.message, error: true);
    }
  }

  Future<void> _loadLanguage() async {
    setState(() => _langLoading = true);
    try {
      final s = await ref.read(apiProvider).translateStatus();
      setState(() {
        _langCode = (s['user_language'] ?? '').toString();
        // 'available' means a real translation backend (keys present); a
        // passthrough still lets the user pick, so we only gate the hint text.
        _translateAvailable = s['available'] == true;
      });
    } catch (_) {
      setState(() => _langCode = '');
    } finally {
      if (mounted) setState(() => _langLoading = false);
    }
  }

  Future<void> _setLanguage(String code) async {
    final prev = _langCode;
    setState(() => _langCode = code);
    try {
      final r = await ref.read(apiProvider).setUserLanguage(code);
      final name = (r['user_language_name'] ?? code).toString();
      if (mounted) showSnack(context, 'Translating visitor speech to $name');
    } on ApiException catch (e) {
      setState(() => _langCode = prev); // revert on failure
      if (mounted) showSnack(context, e.message, error: true);
    }
  }

  @override
  Widget build(BuildContext context) {
    final mode = ref.watch(modeProvider);
    final themeChoice = ref.watch(themeProvider);
    final textScale = MediaQuery.textScalerOf(context).scale(16) / 16;
    final reduceMotion = context.reduceMotion;

    return Scaffold(
      backgroundColor: Colors.transparent,
      appBar: AppBar(title: const Text('Settings')),
      body: MeshScaffoldBody(
        child: SafeArea(
          bottom: false,
          child: ContentWidth(
            child: ListView(
              // 120 bottom clears the floating glass nav pill.
              padding: const EdgeInsets.fromLTRB(T.s16, T.s16, T.s16, 120),
              children: [
                Entrance(index: 0, child: _section('Server')),
                Entrance(
                  index: 0,
                  child: GlassCard(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    TextField(
                      controller: _url,
                      keyboardType: TextInputType.url,
                      autocorrect: false,
                      decoration: InputDecoration(
                        labelText: 'Server address',
                        hintText: PrefsService.defaultBaseUrl,
                        prefixIcon: const Icon(Icons.dns_outlined),
                      ),
                    ),
                    const SizedBox(height: T.s12),
                    TextField(
                      controller: _token,
                      obscureText: true,
                      autocorrect: false,
                      decoration: const InputDecoration(
                        labelText: 'Access token (optional)',
                        hintText: 'only if the server sets ACCESSAI_TOKEN',
                        prefixIcon: Icon(Icons.key_outlined),
                      ),
                    ),
                    const SizedBox(height: T.s8),
                    Text(
                      'The AccessAI server on your computer. Use its LAN IP and '
                      'port (default :8000). Phone and computer must share Wi‑Fi.',
                      style: Theme.of(context).textTheme.bodySmall,
                    ),
                    const SizedBox(height: T.s12),
                    Row(
                      children: [
                        Expanded(
                          child: OutlinedButton.icon(
                            onPressed: _save,
                            icon: const Icon(Icons.save_outlined),
                            label: const Text('Save'),
                          ),
                        ),
                        const SizedBox(width: T.s12),
                        Expanded(
                          child: FilledButton.icon(
                            onPressed: _testing ? null : _test,
                            icon: _testing
                                ? const SizedBox(
                                    width: 18,
                                    height: 18,
                                    child: CircularProgressIndicator(
                                        strokeWidth: 2))
                                : const Icon(Icons.wifi_tethering),
                            label: Text(_testing ? 'Testing…' : 'Test'),
                          ),
                        ),
                      ],
                    ),
                    if (_health != null || _healthError != null) ...[
                      const SizedBox(height: T.s16),
                      _healthPanel(),
                    ],
                  ],
                ),
              ),
                ),

                const SizedBox(height: T.s20),
                Entrance(index: 1, child: _section('Accessibility mode')),
                Entrance(index: 1, child: _modeSelector(mode)),

                const SizedBox(height: T.s20),
                Entrance(index: 2, child: _section('Voice')),
                Entrance(index: 2, child: _voicePicker()),

                const SizedBox(height: T.s20),
                Entrance(index: 3, child: _section('Translation language')),
                Entrance(index: 3, child: _languagePicker()),

                const SizedBox(height: T.s20),
                Entrance(index: 4, child: _section('Appearance')),
                Entrance(index: 4, child: _themeSelector(themeChoice)),

                const SizedBox(height: T.s20),
                Entrance(index: 5, child: _section('Alerts')),
                Entrance(index: 5, child: const _BackgroundAlertsCard()),

                const SizedBox(height: T.s20),
                Entrance(index: 5, child: _section('Accessibility status')),
                Entrance(
                  index: 5,
                  child: GlassCard(
                    child: Column(
                      children: [
                        _statusRow(
                          Icons.format_size,
                          'Text size',
                          '${(textScale * 100).round()}% of default',
                          'The app follows your system font size.',
                        ),
                        const Divider(),
                        _statusRow(
                          reduceMotion
                              ? Icons.motion_photos_off
                              : Icons.animation,
                          'Motion',
                          reduceMotion ? 'Reduced' : 'Full',
                          reduceMotion
                              ? 'Animations are minimised system-wide; the app '
                                  'honours this.'
                              : 'Smooth animations are on. Turn on "Remove '
                                  'animations" in system settings to reduce them.',
                        ),
                      ],
                    ),
                  ),
                ),
                const SizedBox(height: T.s24),
                Center(
                  child: Text('AccessAI • accessibility-first smart doorbell',
                      style: Theme.of(context).textTheme.bodySmall),
                ),
                const SizedBox(height: T.s24),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _section(String title) => Padding(
        padding: const EdgeInsets.only(left: T.s4, bottom: T.s8),
        child: Text(title.toUpperCase(),
            style: Theme.of(context).textTheme.labelSmall?.copyWith(
                color: T.muted,
                fontWeight: FontWeight.w700,
                letterSpacing: 1.4)),
      );

  Widget _healthPanel() {
    if (_healthError != null) {
      return Container(
        width: double.infinity,
        padding: const EdgeInsets.all(T.s12),
        decoration: BoxDecoration(
          color: T.danger.withValues(alpha: 0.14),
          borderRadius: BorderRadius.circular(T.rSm),
          border: Border.all(color: T.danger.withValues(alpha: 0.5)),
        ),
        child: Row(
          children: [
            const Icon(Icons.error_outline, color: T.danger),
            const SizedBox(width: T.s8),
            Expanded(child: Text(_healthError!)),
          ],
        ),
      ).animate(target: context.reduceMotion ? 0 : 1).fadeIn(duration: T.med);
    }
    final s = _health!;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            const StatusPill(
                label: 'Connected', color: T.success, icon: Icons.check_circle),
            const SizedBox(width: T.s8),
            Expanded(
              child: Text('torch ${s.torchVersion} • TTS ${s.ttsEngine}',
                  style: Theme.of(context).textTheme.bodySmall,
                  overflow: TextOverflow.ellipsis),
            ),
          ],
        ),
        const SizedBox(height: T.s12),
        Wrap(
          spacing: T.s8,
          runSpacing: T.s8,
          children: [
            for (final m in s.modules)
              StatusPill(
                label: m.name,
                color: stateColor(m.state),
                semanticLabel: '${m.name}: ${m.state}. ${m.detail}',
              ),
          ],
        ),
      ],
    )
        .animate(target: context.reduceMotion ? 0 : 1)
        .fadeIn(duration: T.med)
        .slideY(begin: 0.05, end: 0);
  }

  Widget _modeSelector(String mode) {
    const options = [
      ('blind', 'Blind', Icons.record_voice_over, 'Spoken announcements'),
      ('deaf', 'Deaf', Icons.vibration, 'Visual + vibration'),
      ('both', 'Both', Icons.all_inclusive, 'Spoken + visual'),
    ];
    return Column(
      children: [
        for (final o in options)
          Padding(
            padding: const EdgeInsets.only(bottom: T.s8),
            child: _RadioTile(
              selected: mode == o.$1,
              icon: o.$3,
              title: o.$2,
              subtitle: o.$4,
              onTap: () => ref.read(modeProvider.notifier).set(o.$1),
            ),
          ),
      ],
    );
  }

  Widget _voicePicker() {
    return GlassCard(
      child: _voicesLoading
          ? const Padding(
              padding: EdgeInsets.symmetric(vertical: T.s8),
              child: LinearProgressIndicator())
          : _voices.isEmpty
              ? Row(
                  children: [
                    const Icon(Icons.info_outline),
                    const SizedBox(width: T.s8),
                    const Expanded(
                        child: Text('No voices reported by the server.')),
                    TextButton(
                        onPressed: _loadVoices, child: const Text('Reload')),
                  ],
                )
              : Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('Spoken voice',
                        style: Theme.of(context).textTheme.labelLarge),
                    const SizedBox(height: T.s8),
                    DropdownButtonFormField<String>(
                      initialValue: _voices.any((v) => v.id == _currentVoice)
                          ? _currentVoice
                          : _voices.first.id,
                      isExpanded: true,
                      decoration: const InputDecoration(
                        border: OutlineInputBorder(),
                        prefixIcon: Icon(Icons.graphic_eq),
                      ),
                      items: [
                        for (final v in _voices)
                          DropdownMenuItem(
                            value: v.id,
                            enabled: v.available,
                            child: Text(
                              v.available ? v.label : '${v.label} (unavailable)',
                              overflow: TextOverflow.ellipsis,
                            ),
                          ),
                      ],
                      onChanged: (id) {
                        if (id != null) _setVoice(id);
                      },
                    ),
                  ],
                ),
    );
  }

  Widget _languagePicker() {
    // Ensure the current code is always a valid dropdown value.
    final selected = _languages.any((l) => l.code == _langCode)
        ? _langCode
        : 'ml';
    return GlassCard(
      child: _langLoading
          ? const Padding(
              padding: EdgeInsets.symmetric(vertical: T.s8),
              child: LinearProgressIndicator())
          : Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('Translate visitor speech into',
                    style: Theme.of(context).textTheme.labelLarge),
                const SizedBox(height: T.s8),
                Semantics(
                  label: 'Translation language',
                  child: DropdownButtonFormField<String>(
                    initialValue: selected,
                    isExpanded: true,
                    decoration: const InputDecoration(
                      border: OutlineInputBorder(),
                      prefixIcon: Icon(Icons.translate),
                    ),
                    items: [
                      for (final l in _languages)
                        DropdownMenuItem(
                          value: l.code,
                          child: Text('${l.name}  (${l.code})',
                              overflow: TextOverflow.ellipsis),
                        ),
                    ],
                    onChanged: (code) {
                      if (code != null) _setLanguage(code);
                    },
                  ),
                ),
                const SizedBox(height: T.s8),
                Text(
                  _translateAvailable
                      ? 'What a visitor says is transcribed, then translated to '
                          'this language before it is shown or spoken to you.'
                      : 'Translation keys are not configured on the server, so '
                          'the original transcript is shown unchanged. You can '
                          'still choose your preferred language here.',
                  style: Theme.of(context).textTheme.bodySmall,
                ),
              ],
            ),
    );
  }

  Widget _themeSelector(AppThemeChoice choice) {
    return GlassCard(
      child: Column(
        children: [
          for (final c in AppThemeChoice.values)
            _RadioTile(
              selected: choice == c,
              icon: switch (c) {
                AppThemeChoice.system => Icons.brightness_auto,
                AppThemeChoice.light => Icons.light_mode,
                AppThemeChoice.dark => Icons.dark_mode,
                AppThemeChoice.highContrast => Icons.contrast,
              },
              title: c.label,
              subtitle: c == AppThemeChoice.highContrast
                  ? 'Maximum contrast for low vision'
                  : null,
              onTap: () => ref.read(themeProvider.notifier).set(c),
              dense: true,
            ),
        ],
      ),
    );
  }

  Widget _statusRow(
      IconData icon, String title, String value, String detail) {
    final text = Theme.of(context).textTheme;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: T.s8),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(icon),
          const SizedBox(width: T.s12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Text(title, style: text.titleSmall),
                    const Spacer(),
                    Text(value,
                        style: text.labelLarge
                            ?.copyWith(fontWeight: FontWeight.w700)),
                  ],
                ),
                const SizedBox(height: 2),
                Text(detail, style: text.bodySmall),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

/// Background doorbell alerts: keeps the LAN /events socket open with the app
/// closed so the phone still rings. Everything stays on the LAN -- no Firebase,
/// no cloud relay. Android requires a visible persistent notice while the
/// service runs, so the switch names that cost up front instead of surprising
/// the user with a notification they can't explain.
class _BackgroundAlertsCard extends ConsumerWidget {
  const _BackgroundAlertsCard();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final on = ref.watch(backgroundAlertsProvider);
    final text = Theme.of(context).textTheme;
    final cs = Theme.of(context).colorScheme;
    return GlassCard(
      child: Semantics(
        toggled: on,
        label: 'Alert me when the app is closed',
        child: Row(
          children: [
            Icon(on ? Icons.notifications_active : Icons.notifications_off,
                color: on ? cs.primary : cs.onSurfaceVariant),
            const SizedBox(width: T.s12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('Alert me when the app is closed',
                      style: text.titleMedium),
                  const SizedBox(height: 2),
                  Text(
                    on
                        ? 'The doorbell rings this phone even when AccessAI '
                            'is closed. Stays on your local network. Android '
                            'shows a permanent “listening” notice while this '
                            'is on.'
                        : 'AccessAI only alerts you while the app is open.',
                    style:
                        text.bodySmall?.copyWith(color: cs.onSurfaceVariant),
                  ),
                ],
              ),
            ),
            Switch(
              value: on,
              onChanged: (v) async {
                final ok = await ref
                    .read(backgroundAlertsProvider.notifier)
                    .setEnabled(v);
                if (!context.mounted) return;
                if (v && !ok) {
                  showSnack(
                      context,
                      'Notification permission is needed to alert you with '
                      'the app closed. Enable it in system settings.',
                      error: true);
                } else {
                  showSnack(
                      context,
                      ok
                          ? 'You’ll be alerted with the app closed'
                          : 'Background alerts off');
                }
              },
            ),
          ],
        ),
      ),
    );
  }
}

class _RadioTile extends StatelessWidget {
  const _RadioTile({
    required this.selected,
    required this.icon,
    required this.title,
    required this.onTap,
    this.subtitle,
    this.dense = false,
  });

  final bool selected;
  final IconData icon;
  final String title;
  final String? subtitle;
  final VoidCallback onTap;
  final bool dense;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    return Semantics(
      button: true,
      selected: selected,
      label: '$title${subtitle != null ? '. $subtitle' : ''}',
      child: Material(
        color: selected
            ? cs.primary.withValues(alpha: 0.14)
            : Colors.transparent,
        borderRadius: BorderRadius.circular(T.rMd),
        child: InkWell(
          borderRadius: BorderRadius.circular(T.rMd),
          onTap: onTap,
          child: Container(
            constraints: BoxConstraints(minHeight: dense ? 56 : T.minTouch),
            padding: const EdgeInsets.symmetric(
                horizontal: T.s12, vertical: T.s8),
            child: Row(
              children: [
                Icon(icon,
                    color: selected ? cs.primary : cs.onSurfaceVariant),
                const SizedBox(width: T.s16),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      Text(title,
                          style: Theme.of(context)
                              .textTheme
                              .titleMedium
                              ?.copyWith(
                                  fontWeight: selected
                                      ? FontWeight.w700
                                      : FontWeight.w500)),
                      if (subtitle != null)
                        Text(subtitle!,
                            style: Theme.of(context).textTheme.bodySmall),
                    ],
                  ),
                ),
                if (selected) Icon(Icons.check_circle, color: cs.primary),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
