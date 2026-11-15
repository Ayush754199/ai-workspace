import 'package:flutter/material.dart';

import 'chat_workspace_screen.dart';

/// Compatibility entry point for callers that still reference the prototype
/// home screen. The mobile app now uses the backend-backed Chat v2 workspace.
class HomeScreen extends StatelessWidget {
  const HomeScreen({super.key});

  @override
  Widget build(BuildContext context) => const ChatWorkspaceScreen();
}
