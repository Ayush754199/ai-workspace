import 'package:flutter/material.dart';
import 'package:local_auth/local_auth.dart';
import 'package:provider/provider.dart';

import 'services/api_service.dart';
import 'services/auth_store.dart';
import 'screens/auth_screen.dart';
import 'screens/chat_workspace_screen.dart';
import 'theme/app_theme.dart';
import 'widgets/brand_logo.dart';

void main() => runApp(const AIWorkspaceApp());

class AIWorkspaceApp extends StatelessWidget {
  const AIWorkspaceApp({super.key});

  @override
  Widget build(BuildContext context) => MultiProvider(
        providers: [
          Provider(create: (_) => ApiService()),
          ChangeNotifierProvider(create: (_) => AuthStore()),
        ],
        child: MaterialApp(
          title: AppBrand.name,
          theme: AppTheme.light(),
          darkTheme: AppTheme.dark(),
          themeMode: ThemeMode.system,
          home: const StartupGate(),
        ),
      );
}

class StartupGate extends StatefulWidget {
  const StartupGate({super.key});
  @override
  State<StartupGate> createState() => _StartupGateState();
}

class _StartupGateState extends State<StartupGate> {
  bool checking = true;
  bool unlocked = false;

  @override
  void initState() {
    super.initState();
    _unlock();
  }

  Future<void> _unlock() async {
    final store = context.read<AuthStore>();
    await store.restore(context.read<ApiService>());
    if (!store.isAuthenticated) {
      if (mounted)
        setState(() {
          unlocked = true;
          checking = false;
        });
      return;
    }
    final auth = LocalAuthentication();
    try {
      unlocked = await auth.authenticate(
        localizedReason: 'Unlock AI Workspace',
      );
    } catch (_) {
      unlocked = true;
    }
    if (mounted) setState(() => checking = false);
  }

  @override
  Widget build(BuildContext context) {
    if (checking)
      return const Scaffold(body: Center(child: CircularProgressIndicator()));
    if (!unlocked)
      return Scaffold(
        body: Center(
          child: FilledButton(onPressed: _unlock, child: const Text('Unlock')),
        ),
      );
    return Consumer<AuthStore>(
      builder: (_, store, __) => store.token == null
          ? const AuthScreen()
          : const ChatWorkspaceScreen(),
    );
  }
}
