import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../services/api_service.dart';
import '../services/auth_store.dart';

class AdminScreen extends StatefulWidget {
  const AdminScreen({super.key});
  @override
  State<AdminScreen> createState() => _AdminScreenState();
}

class _AdminScreenState extends State<AdminScreen> {
  int? selectedRoom;
  Map<String, dynamic>? transcript;

  @override
  Widget build(BuildContext context) {
    final api = context.read<ApiService>();
    final token = context.read<AuthStore>().token!;
    return Scaffold(appBar: AppBar(title: const Text('Admin surveillance')), body: Padding(padding: const EdgeInsets.all(16), child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      const Text('Chat transcripts and audit activity', style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold)),
      const SizedBox(height: 8),
      const Text('Admin access to chats is logged.'),
      const SizedBox(height: 20),
      FilledButton.icon(onPressed: () async { await api.exportAudit(token); if (context.mounted) ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Audit CSV downloaded'))); }, icon: const Icon(Icons.download), label: const Text('Export CSV')),
      Expanded(child: FutureBuilder<List<dynamic>>(future: api.adminChats(token), builder: (_, snapshot) { if (!snapshot.hasData) return const Center(child: CircularProgressIndicator()); return ListView(children: snapshot.data!.map((room) => ListTile(title: Text(room['name']), subtitle: Text('${room['room_type']} · ${room['members'].length} members'), selected: selectedRoom == room['id'], onTap: () async { transcript = Map<String, dynamic>.from(await api.adminTranscript(token, room['id'])); selectedRoom = room['id']; if (mounted) setState(() {}); })).toList()); })),
      if (transcript != null) Expanded(child: ListView(children: (transcript!['messages'] as List).map((message) => ListTile(title: Text('${message['sender_name']} (${message['sender_role']})'), subtitle: Text(message['content']))).toList())),
    ])));
  }
}