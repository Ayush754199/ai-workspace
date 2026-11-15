import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../services/api_service.dart';
import '../services/auth_store.dart';
import '../widgets/nexora_motion.dart';

class AdminScreen extends StatefulWidget {
  const AdminScreen({super.key});
  @override
  State<AdminScreen> createState() => _AdminScreenState();
}

class _AdminScreenState extends State<AdminScreen> {
  late Future<_AdminData> data;
  int? selectedRoom;
  Map<String, dynamic>? transcript;

  @override
  void initState() {
    super.initState();
    data = _load();
  }

  Future<_AdminData> _load() async {
    final api = context.read<ApiService>();
    final token = context.read<AuthStore>().token!;
    final result = await Future.wait([
      api.adminAnalytics(token),
      api.adminUsers(token),
      api.adminSettings(token),
      api.adminChats(token),
    ]);
    return _AdminData(
      analytics: Map<String, dynamic>.from(result[0] as Map),
      users: (result[1] as List)
          .whereType<Map>()
          .map(Map<String, dynamic>.from)
          .toList(),
      settings: Map<String, dynamic>.from(result[2] as Map),
      chats: (result[3] as List)
          .whereType<Map>()
          .map(Map<String, dynamic>.from)
          .toList(),
    );
  }

  void _refresh() => setState(() => data = _load());

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(
          title: const Text('Admin console'),
          actions: [
            IconButton(onPressed: _refresh, icon: const Icon(Icons.refresh))
          ],
        ),
        body: FutureBuilder<_AdminData>(
          future: data,
          builder: (context, snapshot) {
            if (snapshot.connectionState != ConnectionState.done) {
              return const Center(child: NexoraMotion(size: 120));
            }
            if (snapshot.hasError) {
              return Center(
                  child: Text('Admin data unavailable: ${snapshot.error}'));
            }
            final value = snapshot.data!;
            return ListView(
              padding: const EdgeInsets.all(16),
              children: [
                const Text('Workspace overview',
                    style:
                        TextStyle(fontSize: 22, fontWeight: FontWeight.w700)),
                const SizedBox(height: 12),
                _analytics(value.analytics),
                const SizedBox(height: 18),
                _settings(value.settings),
                const SizedBox(height: 18),
                _users(value.users),
                const SizedBox(height: 18),
                _audit(value.chats),
              ],
            );
          },
        ),
      );

  Widget _analytics(Map<String, dynamic> values) => Card(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Wrap(
            spacing: 24,
            runSpacing: 12,
            children: values.entries
                .map((entry) => Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(entry.key,
                            style: const TextStyle(color: Colors.grey)),
                        Text('${entry.value}',
                            style: const TextStyle(
                                fontSize: 22, fontWeight: FontWeight.bold))
                      ],
                    ))
                .toList(),
          ),
        ),
      );

  Widget _settings(Map<String, dynamic> values) => Card(
        child: ExpansionTile(
          initiallyExpanded: true,
          title: const Text('Admin settings',
              style: TextStyle(fontWeight: FontWeight.w700)),
          subtitle: const Text('Feature flags and workspace configuration'),
          children: values.entries
              .map((entry) => SwitchListTile(
                    value: entry.value == true,
                    onChanged: null,
                    title: Text(entry.key),
                    subtitle: Text('${entry.value}'),
                  ))
              .toList(),
        ),
      );

  Widget _users(List<Map<String, dynamic>> users) => Card(
        child: ExpansionTile(
          title: Text('Users (${users.length})',
              style: const TextStyle(fontWeight: FontWeight.w700)),
          children: users
              .take(20)
              .map((user) => ListTile(
                    leading:
                        const CircleAvatar(child: Icon(Icons.person_outline)),
                    title: Text(user['name']?.toString() ??
                        user['phone']?.toString() ??
                        'User'),
                    subtitle: Text(
                        '${user['role'] ?? 'user'} · ${user['is_active'] == false ? 'inactive' : 'active'}'),
                  ))
              .toList(),
        ),
      );

  Widget _audit(List<Map<String, dynamic>> chats) => Card(
        child: ExpansionTile(
          title: const Text('Audit and transcripts',
              style: TextStyle(fontWeight: FontWeight.w700)),
          children: [
            ListTile(
              leading: const Icon(Icons.download_outlined),
              title: const Text('Export audit CSV'),
              onTap: () async {
                await context
                    .read<ApiService>()
                    .exportAudit(context.read<AuthStore>().token!);
                if (mounted)
                  ScaffoldMessenger.of(context).showSnackBar(
                      const SnackBar(content: Text('Audit CSV downloaded')));
              },
            ),
            ...chats.map((room) => ListTile(
                  title: Text(room['name']?.toString() ?? 'Chat'),
                  subtitle: Text(
                      '${room['room_type'] ?? 'room'} · ${(room['members'] as List?)?.length ?? 0} members'),
                  selected: selectedRoom == room['id'],
                  onTap: () async {
                    final result = await context
                        .read<ApiService>()
                        .adminTranscript(context.read<AuthStore>().token!,
                            room['id'] as int);
                    if (mounted)
                      setState(() {
                        selectedRoom = room['id'] as int;
                        transcript = Map<String, dynamic>.from(result);
                      });
                  },
                )),
            if (transcript != null) ...[
              const Divider(),
              ...(transcript!['messages'] as List? ?? const [])
                  .map((message) => ListTile(
                        title: Text(
                            '${message['sender_name'] ?? 'Member'} (${message['sender_role'] ?? 'user'})'),
                        subtitle: Text(message['content']?.toString() ?? ''),
                      )),
            ],
          ],
        ),
      );
}

class _AdminData {
  const _AdminData(
      {required this.analytics,
      required this.users,
      required this.settings,
      required this.chats});
  final Map<String, dynamic> analytics;
  final List<Map<String, dynamic>> users;
  final Map<String, dynamic> settings;
  final List<Map<String, dynamic>> chats;
}
