import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../services/api_service.dart';
import '../services/auth_store.dart';

class CallsScreen extends StatefulWidget {
  const CallsScreen({super.key});

  @override
  State<CallsScreen> createState() => _CallsScreenState();
}

class _CallsScreenState extends State<CallsScreen> {
  List<Map<String, dynamic>> calls = [];
  String? error;

  ApiService get api => context.read<ApiService>();
  AuthStore get auth => context.read<AuthStore>();

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final result = await api.calls(auth.token!);
      calls = result
          .whereType<Map>()
          .map((item) => Map<String, dynamic>.from(item))
          .toList();
      error = null;
    } catch (exception) {
      error = exception.toString().replaceFirst('Exception: ', '');
    }
    if (mounted) setState(() {});
  }

  Future<void> _action(String id, String action) async {
    try {
      await api.callAction(auth.token!, id, action);
      await _load();
    } catch (exception) {
      setState(
          () => error = exception.toString().replaceFirst('Exception: ', ''));
    }
  }

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(title: const Text('Calls'), actions: [
          IconButton(onPressed: _load, icon: const Icon(Icons.refresh))
        ]),
        body: RefreshIndicator(
            onRefresh: _load,
            child: ListView(padding: const EdgeInsets.all(16), children: [
              if (error != null)
                Text(error!, style: const TextStyle(color: Colors.red)),
              if (calls.isEmpty)
                const Padding(
                    padding: EdgeInsets.all(40),
                    child: Center(child: Text('No calls yet.'))),
              ...calls.map(_callCard)
            ])),
      );

  Widget _callCard(Map<String, dynamic> call) {
    final id = call['call_id']?.toString() ?? '';
    final status = call['status']?.toString() ?? 'unknown';
    final active =
        status == 'ringing' || status == 'accepted' || status == 'active';
    return Card(
        child: ListTile(
            leading: Icon(call['call_type'] == 'video'
                ? Icons.videocam_outlined
                : Icons.call_outlined),
            title: Text('${call['call_type'] ?? 'audio'} call'),
            subtitle: Text('$status • ${call['created_at'] ?? ''}'),
            trailing: active
                ? PopupMenuButton<String>(
                    onSelected: (action) => _action(id, action),
                    itemBuilder: (_) => const [
                          PopupMenuItem(value: 'accept', child: Text('Accept')),
                          PopupMenuItem(value: 'end', child: Text('End')),
                          PopupMenuItem(value: 'reject', child: Text('Reject'))
                        ])
                : null));
  }
}
