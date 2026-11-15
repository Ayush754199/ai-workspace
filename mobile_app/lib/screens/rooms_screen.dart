import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../services/api_service.dart';
import '../services/auth_store.dart';
import '../widgets/nexora_motion.dart';

class RoomsScreen extends StatefulWidget {
  const RoomsScreen({super.key});
  @override
  State<RoomsScreen> createState() => _RoomsScreenState();
}

class _RoomsScreenState extends State<RoomsScreen> {
  List<Map<String, dynamic>> rooms = [];
  List<Map<String, dynamic>> messages = [];
  Map<String, dynamic>? selected;
  final composer = TextEditingController();
  bool loading = true;
  bool sending = false;
  String? error;

  ApiService get api => context.read<ApiService>();
  AuthStore get auth => context.read<AuthStore>();

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    composer.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final result = await api.rooms(auth.token!);
      rooms = result
          .whereType<Map>()
          .map((item) => Map<String, dynamic>.from(item))
          .toList();
      if (rooms.isNotEmpty) await _open(rooms.first);
    } catch (exception) {
      error = exception.toString().replaceFirst('Exception: ', '');
    }
    if (mounted) setState(() => loading = false);
  }

  Future<void> _open(Map<String, dynamic> room) async {
    final id = int.tryParse(room['id'].toString());
    if (id == null) return;
    setState(() => selected = room);
    try {
      final result = await api.messages(auth.token!, id);
      messages = result
          .whereType<Map>()
          .map((item) => Map<String, dynamic>.from(item))
          .toList();
    } catch (exception) {
      error = exception.toString().replaceFirst('Exception: ', '');
    }
    if (mounted) setState(() {});
  }

  Future<void> _send() async {
    final text = composer.text.trim();
    final id = int.tryParse(selected?['id']?.toString() ?? '');
    if (text.isEmpty || id == null || sending) return;
    composer.clear();
    setState(() => sending = true);
    try {
      await api.sendMessage(auth.token!, id, text);
      final result = await api.messages(auth.token!, id);
      messages = result
          .whereType<Map>()
          .map((item) => Map<String, dynamic>.from(item))
          .toList();
    } catch (exception) {
      error = exception.toString().replaceFirst('Exception: ', '');
    }
    if (mounted) setState(() => sending = false);
  }

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(title: const Text('Chat rooms')),
        body: loading
            ? const Center(child: NexoraMotion(size: 110))
            : Row(children: [
                SizedBox(width: 170, child: _roomList()),
                const VerticalDivider(width: 1),
                Expanded(child: _roomChat()),
              ]),
      );

  Widget _roomList() => ListView.builder(
        padding: const EdgeInsets.symmetric(vertical: 10),
        itemCount: rooms.length,
        itemBuilder: (_, index) {
          final room = rooms[index];
          final title = room['name']?.toString() ?? 'Room ${room['id']}';
          return ListTile(
            selected: selected?['id'] == room['id'],
            leading: const CircleAvatar(child: Icon(Icons.groups_2_outlined)),
            title: Text(title, maxLines: 1, overflow: TextOverflow.ellipsis),
            subtitle: Text(room['room_type']?.toString() ?? 'Team room'),
            onTap: () => _open(room),
          );
        },
      );

  Widget _roomChat() {
    if (selected == null) {
      return const Center(child: Text('Choose a room to start collaborating.'));
    }
    return Column(children: [
      ListTile(
        title: Text(selected?['name']?.toString() ?? 'Room'),
        subtitle: const Text('A shared space for your team'),
        leading: const Icon(Icons.forum_outlined),
      ),
      if (error != null)
        Padding(
            padding: const EdgeInsets.all(8),
            child: Text(error!, style: const TextStyle(color: Colors.red))),
      Expanded(
        child: messages.isEmpty
            ? const Center(child: Text('No messages yet. Say hello.'))
            : ListView.builder(
                padding: const EdgeInsets.all(16),
                itemCount: messages.length,
                itemBuilder: (_, index) {
                  final message = messages[index];
                  return Card(
                    child: ListTile(
                      title:
                          Text(message['sender_name']?.toString() ?? 'Member'),
                      subtitle: Text(message['content']?.toString() ?? ''),
                    ),
                  );
                },
              ),
      ),
      SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(10),
          child: Row(children: [
            Expanded(
                child: TextField(
                    controller: composer,
                    decoration:
                        const InputDecoration(hintText: 'Message the room'))),
            IconButton.filled(
                onPressed: sending ? null : _send,
                icon: const Icon(Icons.arrow_upward)),
          ]),
        ),
      ),
    ]);
  }
}
