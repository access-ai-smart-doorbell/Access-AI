import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../core/glass.dart';
import '../core/motion.dart';
import '../core/tokens.dart';
import '../services/api_service.dart';
import '../services/discovery_service.dart';
import '../services/prefs_service.dart';
import '../state/providers.dart';
import '../widgets/doorbell_hero.dart';

/// First-run connection screen. The one thing the app can't guess is where the
/// AccessAI server lives, so we ask for it up front, test it, and only then
/// enter the app. The user can also continue without a successful test (e.g. to
/// configure it later) — nothing here gates functionality permanently.
class OnboardingScreen extends ConsumerStatefulWidget {
  const OnboardingScreen({super.key});

  @override
  ConsumerState<OnboardingScreen> createState() => _OnboardingScreenState();
}

class _OnboardingScreenState extends ConsumerState<OnboardingScreen> {
  late final TextEditingController _url;
  bool _connecting = false;
  bool _scanning = false;
  double _scanProgress = 0.0;
  String? _error;
  String? _scanStatus;
  bool _testedButFailed = false;

  @override
  void initState() {
    super.initState();
    _url = TextEditingController(text: PrefsService.defaultBaseUrl);
  }

  @override
  void dispose() {
    _url.dispose();
    super.dispose();
  }

  Future<void> _connect() async {
    final url = PrefsService.normalizeUrl(_url.text.trim());
    if (url.isEmpty) {
      setState(() => _error = 'Enter the server address');
      return;
    }
    setState(() {
      _connecting = true;
      _error = null;
    });
    final probe = ApiService(url);
    try {
      final status = await probe.status();
      // Success — persist (flips isConfigured) so the root swaps to the app.
      await ref.read(baseUrlProvider.notifier).set(url);
      ref.read(modeProvider.notifier).adopt(status.mode);
    } on ApiException catch (e) {
      setState(() {
        _error = e.message;
        _testedButFailed = true;
      });
    } finally {
      probe.close();
      if (mounted) setState(() => _connecting = false);
    }
  }

  Future<void> _autoDetect() async {
    setState(() {
      _scanning = true;
      _scanProgress = 0.0;
      _scanStatus = 'Scanning your Wi‑Fi network…';
      _error = null;
      _testedButFailed = false;
    });

    final prefs = ref.read(prefsProvider);
    final token = prefs.authToken;
    final knownUrl = prefs.baseUrl; // try last-known IP first

    final found = await DiscoveryService.scan(
      token: token,
      knownIp: knownUrl.isNotEmpty ? knownUrl : null,
      onProgress: (p) {
        if (mounted) {
          setState(() {
            _scanProgress = p;
            if (p < 0.5) {
              _scanStatus = 'Scanning your Wi‑Fi network…';
            } else if (p < 0.9) {
              _scanStatus = 'Almost done…';
            } else {
              _scanStatus = 'Finishing up…';
            }
          });
        }
      },
    );

    if (!mounted) return;

    if (found != null) {
      setState(() {
        _url.text = found;
        _scanning = false;
        _scanStatus = null;
        _scanProgress = 0;
        _error = null;
      });
      // Auto-connect to the discovered server.
      await _connect();
    } else {
      setState(() {
        _scanning = false;
        _scanStatus = null;
        _scanProgress = 0;
        _error =
            'No AccessAI server found on your Wi‑Fi. Make sure the server is '
            'running on your laptop and both devices are on the same network.';
        _testedButFailed = false;
      });
    }
  }

  Future<void> _continueAnyway() async {
    final url = PrefsService.normalizeUrl(_url.text.trim());
    await ref.read(baseUrlProvider.notifier).set(url);
  }

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return Scaffold(
      body: Stack(
        children: [
          Positioned.fill(child: GradientMesh(animate: !context.reduceMotion)),
          SafeArea(
            child: Center(
              child: SingleChildScrollView(
                padding: const EdgeInsets.all(T.s24),
                child: ConstrainedBox(
                  constraints: const BoxConstraints(maxWidth: 520),
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      Entrance(
                        index: 0,
                        child: Center(
                          child: AuroraRing(
                            size: 200,
                            thickness: 4,
                            active: true,
                            child: const DoorbellHero(size: 150),
                          ),
                        ),
                      ),
                      const SizedBox(height: T.s20),
                      Entrance(
                        index: 1,
                        child: Text('AccessAI',
                            textAlign: TextAlign.center,
                            style: text.displaySmall?.copyWith(
                                fontWeight: FontWeight.w800,
                                letterSpacing: -1.0)),
                      ),
                      const SizedBox(height: T.s8),
                      Entrance(
                        index: 2,
                        child: Text(
                          'Your accessible eyes and ears at the door.',
                          textAlign: TextAlign.center,
                          style: text.titleMedium?.copyWith(color: T.muted),
                        ),
                      ),
                      const SizedBox(height: T.s32),
                      Entrance(
                        index: 3,
                        child: GlassCard(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text('Connect to your server',
                                  style: text.titleMedium),
                              const SizedBox(height: T.s8),
                              Text(
                                'Tap Auto‑Detect to find the server '
                                'automatically, or enter the address manually. '
                                'Your phone and laptop must be on the same Wi‑Fi.',
                                style: text.bodySmall,
                              ),
                              const SizedBox(height: T.s16),

                              // ── Auto-detect button ─────────────────────────
                              SizedBox(
                                height: T.minTouch,
                                width: double.infinity,
                                child: OutlinedButton.icon(
                                  onPressed:
                                      (_scanning || _connecting) ? null : _autoDetect,
                                  icon: _scanning
                                      ? const SizedBox(
                                          width: 18,
                                          height: 18,
                                          child: CircularProgressIndicator(
                                              strokeWidth: 2))
                                      : const Icon(Icons.wifi_find_outlined),
                                  label: Text(_scanning
                                      ? 'Scanning…'
                                      : 'Auto‑Detect Server'),
                                ),
                              ),

                              // ── Scan progress bar ──────────────────────────
                              if (_scanning) ...[
                                const SizedBox(height: T.s12),
                                ClipRRect(
                                  borderRadius: BorderRadius.circular(4),
                                  child: LinearProgressIndicator(
                                    value: _scanProgress,
                                    minHeight: 6,
                                  ),
                                ),
                                const SizedBox(height: T.s8),
                                Text(
                                  _scanStatus ?? '',
                                  style: text.bodySmall?.copyWith(
                                    color: T.muted,
                                  ),
                                ),
                              ],

                              const SizedBox(height: T.s16),

                              // ── Divider with "or" ──────────────────────────
                              Row(
                                children: [
                                  const Expanded(child: Divider()),
                                  Padding(
                                    padding: const EdgeInsets.symmetric(
                                        horizontal: T.s8),
                                    child: Text('or enter manually',
                                        style: text.bodySmall
                                            ?.copyWith(color: T.muted)),
                                  ),
                                  const Expanded(child: Divider()),
                                ],
                              ),

                              const SizedBox(height: T.s16),

                              // ── Manual URL field ───────────────────────────
                              TextField(
                                controller: _url,
                                keyboardType: TextInputType.url,
                                autocorrect: false,
                                onSubmitted: (_) => _connect(),
                                decoration: const InputDecoration(
                                  labelText: 'Server address',
                                  hintText: 'http://192.168.x.x:8000',
                                  prefixIcon: Icon(Icons.dns_outlined),
                                ),
                              ),

                              if (_error != null) ...[
                                const SizedBox(height: T.s12),
                                Row(
                                  children: [
                                    const Icon(Icons.error_outline,
                                        color: T.danger, size: 20),
                                    const SizedBox(width: T.s8),
                                    Expanded(
                                        child: Text(_error!,
                                            style: text.bodySmall?.copyWith(
                                                color: T.danger))),
                                  ],
                                ),
                              ],
                              const SizedBox(height: T.s16),
                              SizedBox(
                                height: T.minTouch,
                                child: FilledButton.icon(
                                  onPressed:
                                      (_connecting || _scanning) ? null : _connect,
                                  icon: _connecting
                                      ? const SizedBox(
                                          width: 18,
                                          height: 18,
                                          child: CircularProgressIndicator(
                                              strokeWidth: 2))
                                      : const Icon(Icons.wifi_tethering),
                                  label: Text(
                                      _connecting ? 'Connecting…' : 'Connect'),
                                ),
                              ),
                              if (_testedButFailed) ...[
                                const SizedBox(height: T.s8),
                                TextButton(
                                  onPressed: _continueAnyway,
                                  child: const Text('Continue anyway'),
                                ),
                              ],
                            ],
                          ),
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}
