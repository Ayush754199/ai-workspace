import 'dart:ui';
import 'package:camera/camera.dart';
import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import 'package:flutter_tts/flutter_tts.dart';
import 'package:image_picker/image_picker.dart';
import 'package:provider/provider.dart';
import 'package:speech_to_text/speech_to_text.dart';
import '../services/api_service.dart';
import '../services/auth_store.dart';
import '../theme/app_theme.dart';
import '../widgets/brand_logo.dart';
import 'admin_screen.dart';

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});
  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  List<dynamic> rooms = [];
  int? selectedRoom;
  List<dynamic> messages = [];
  final input = TextEditingController();
  final speech = SpeechToText();
  final tts = FlutterTts();
  final picker = ImagePicker();
  bool listening = false;
  bool loading = false;
  bool sidebarOpen = true;
  bool hubOpen = true;
  String? summary;
  String? attachmentName;

  ApiService get api => context.read<ApiService>();
  String get token => context.read<AuthStore>().token!;
  String get currentUser => context.read<AuthStore>().user?['full_name'] ?? 'Workspace member';

  @override
  void initState() { super.initState(); loadRooms(); }
  @override
  void dispose() { input.dispose(); super.dispose(); }
  Future<void> loadRooms() async { rooms = await api.rooms(token); if (mounted) setState(() {}); }
  Future<void> openRoom(int id) async { setState(() { selectedRoom = id; loading = true; summary = null; }); messages = await api.messages(token, id); if (mounted) setState(() => loading = false); }
  Future<void> send() async { if (selectedRoom == null || input.text.trim().isEmpty) return; final text = input.text.trim(); input.clear(); await api.sendMessage(token, selectedRoom!, text); await openRoom(selectedRoom!); }
  Future<void> makeSummary() async { if (selectedRoom == null) return; final result = await api.summary(token, selectedRoom!); setState(() => summary = '${result['summary']}\n\nKey decisions\n${(result['key_decisions'] as List).join('\n')}'); }
  Future<void> toggleSpeech() async { if (listening) { await speech.stop(); setState(() => listening = false); return; } if (await speech.initialize()) { setState(() => listening = true); speech.listen(onResult: (result) { input.text = result.recognizedWords; setState(() {}); }); } }
  Future<void> attachFile() async { final result = await FilePicker.platform.pickFiles(type: FileType.custom, allowedExtensions: ['pdf', 'docx', 'png', 'jpg', 'jpeg', 'webp']); if (result != null) setState(() => attachmentName = result.files.single.name); }
  Future<void> takePhoto() async { final photo = await picker.pickImage(source: ImageSource.camera); if (photo != null) setState(() => attachmentName = photo.name); }
  Future<void> cameraScan() async { final cameras = await availableCameras(); if (cameras.isEmpty || !mounted) return; Navigator.push(context, MaterialPageRoute(builder: (_) => CameraScreen(camera: cameras.first))); }
  Future<void> export(String format) async { final bytes = await api.exportDocument(token, format, messages.map((message) => message['content']).join('\n\n')); if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('${format.toUpperCase()} export ready (${bytes.length} bytes)'))); }

  @override
  Widget build(BuildContext context) => Scaffold(backgroundColor: AppTheme.obsidian, body: SafeArea(child: LayoutBuilder(builder: (context, constraints) { final wide = constraints.maxWidth >= 1050; return Column(children: [if (!wide) _mobileTopBar(), Expanded(child: Row(children: [if (wide || sidebarOpen) SizedBox(width: 258, child: _sidebar()), Expanded(child: _chat()), if (wide || hubOpen) SizedBox(width: 270, child: _hub())]))]); })));

  Widget _mobileTopBar() => Container(padding: const EdgeInsets.fromLTRB(16, 14, 16, 10), decoration: const BoxDecoration(border: Border(bottom: BorderSide(color: AppTheme.border))), child: Row(children: [IconButton(onPressed: () => setState(() => sidebarOpen = !sidebarOpen), icon: const Icon(Icons.menu_rounded)), const BrandLogo(compact: true), const Spacer(), IconButton(onPressed: () => setState(() => hubOpen = !hubOpen), icon: const Icon(Icons.folder_copy_outlined))]));

  Widget _sidebar() => Container(color: AppTheme.obsidian, padding: const EdgeInsets.fromLTRB(16, 20, 12, 14), child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [const BrandLogo(), const SizedBox(height: 28), _gradientButton(Icons.add, 'New session', () => setState(() { selectedRoom = null; messages = []; })), const SizedBox(height: 26), const Text('TODAY', style: TextStyle(color: Color(0xff64748b), fontSize: 11, letterSpacing: 1.5, fontWeight: FontWeight.bold)), const SizedBox(height: 9), Expanded(child: ListView(children: rooms.map((room) => _roomTile(room)).toList())), Container(padding: const EdgeInsets.all(12), decoration: BoxDecoration(color: AppTheme.onyx, borderRadius: BorderRadius.circular(14), border: Border.all(color: AppTheme.border)), child: Row(children: [const CircleAvatar(radius: 18, backgroundColor: AppTheme.violet, child: Icon(Icons.person_outline, size: 18)), const SizedBox(width: 9), Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [Text(currentUser, maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(fontWeight: FontWeight.w600)), const Text('Memory online', style: TextStyle(color: AppTheme.cyan, fontSize: 11))])), IconButton(onPressed: () => context.read<AuthStore>().logout(), icon: const Icon(Icons.logout, size: 17))]))]));

  Widget _roomTile(dynamic room) { final active = selectedRoom == room['id']; return Padding(padding: const EdgeInsets.only(bottom: 6), child: ListTile(shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)), tileColor: active ? AppTheme.indigo.withOpacity(.22) : Colors.transparent, leading: Icon(active ? Icons.forum : Icons.chat_bubble_outline, color: active ? AppTheme.cyan : const Color(0xff64748b), size: 19), title: Text(room['name'], maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(fontSize: 13)), subtitle: Text('${room['member_details'].length} members', style: const TextStyle(fontSize: 11, color: Color(0xff64748b))), onTap: () => openRoom(room['id']))); }

  Widget _chat() => Container(decoration: const BoxDecoration(border: Border(left: BorderSide(color: AppTheme.border), right: BorderSide(color: AppTheme.border))), child: Column(children: [_chatHeader(), Expanded(child: selectedRoom == null ? _welcome() : _messages()), _inputBar()]));
  Widget _chatHeader() => Padding(padding: const EdgeInsets.symmetric(horizontal: 22, vertical: 17), child: Row(children: [if (selectedRoom != null) const Icon(Icons.lock_outline, color: AppTheme.cyan, size: 17), const SizedBox(width: 9), Expanded(child: Text(selectedRoom == null ? 'Command center' : (rooms.firstWhere((room) => room['id'] == selectedRoom, orElse: () => {'name': 'Session'})['name']), style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 16))), if (selectedRoom != null) IconButton(onPressed: makeSummary, tooltip: 'Generate MoM', icon: const Icon(Icons.auto_awesome, color: AppTheme.cyan)), IconButton(onPressed: () {}, icon: const Icon(Icons.more_horiz))]));
  Widget _welcome() => SingleChildScrollView(padding: const EdgeInsets.fromLTRB(28, 50, 28, 20), child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [Container(height: 150, decoration: BoxDecoration(borderRadius: BorderRadius.circular(24), gradient: RadialGradient(colors: [AppTheme.violet.withOpacity(.32), AppTheme.obsidian], radius: 1.1)), child: const Center(child: BrandLogo())), const SizedBox(height: 30), const Text('Good evening.', style: TextStyle(color: Color(0xff94a3b8), fontSize: 16)), const SizedBox(height: 4), const Text('What are we building today?', style: TextStyle(fontSize: 29, fontWeight: FontWeight.w700)), const SizedBox(height: 24), Wrap(spacing: 10, runSpacing: 10, children: ['Analyze Handwritten PDF', 'Export Excel Sheet', 'Read Math Notes'].map((label) => ActionChip(label: Text(label), avatar: const Icon(Icons.arrow_outward, size: 15), onPressed: () => input.text = label)).toList())]));
  Widget _messages() => ListView.builder(padding: const EdgeInsets.fromLTRB(24, 15, 24, 20), itemCount: messages.length + (summary == null ? 0 : 1), itemBuilder: (_, index) { if (summary != null && index == messages.length) return _glassCard(Icons.auto_awesome, 'Meeting intelligence', summary!); final message = messages[index]; final own = message['sender_id'] == context.read<AuthStore>().user?['id']; return Align(alignment: own ? Alignment.centerRight : Alignment.centerLeft, child: Container constraints: const BoxConstraints(maxWidth: 680), margin: const EdgeInsets.only(bottom: 15), padding: const EdgeInsets.all(15), decoration: BoxDecoration(color: own ? const Color(0xff1e1b4b) : AppTheme.onyx, borderRadius: BorderRadius.circular(16), border: Border.all(color: own ? AppTheme.indigo.withOpacity(.5) : AppTheme.border)), child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [Text('${message['sender_name']} (${message['sender_role']})', style: const TextStyle(color: AppTheme.cyan, fontSize: 11, fontWeight: FontWeight.w700)), const SizedBox(height: 6), MarkdownBody(data: message['content'])])); });
  Widget _inputBar() => Padding(padding: const EdgeInsets.fromLTRB(18, 8, 18, 18), child: ClipRRect(borderRadius: BorderRadius.circular(20), child: BackdropFilter(filter: ImageFilter.blur(sigmaX: 12, sigmaY: 12), child: Container(padding: const EdgeInsets.all(8), decoration: BoxDecoration(color: AppTheme.onyx.withOpacity(.92), border: Border.all(color: AppTheme.border), borderRadius: BorderRadius.circular(20)), child: Column(children: [if (attachmentName != null) Align(alignment: Alignment.centerLeft, child: Chip(avatar: const Icon(Icons.attach_file, size: 15), label: Text(attachmentName!), onDeleted: () => setState(() => attachmentName = null))), Row(children: [IconButton(onPressed: attachFile, icon: const Icon(Icons.add_circle_outline)), IconButton(onPressed: takePhoto, icon: const Icon(Icons.camera_alt_outlined)), IconButton(onPressed: toggleSpeech, icon: Icon(listening ? Icons.stop_circle_outlined : Icons.mic_none, color: listening ? AppTheme.cyan : null)), Expanded(child: TextField(controller: input, minLines: 1, maxLines: 4, decoration: const InputDecoration(hintText: 'Ask NEXUS anything...', border: InputBorder.none, filled: false))), Container(decoration: BoxDecoration(shape: BoxShape.circle, gradient: const LinearGradient(colors: [AppTheme.violet, AppTheme.indigo]), boxShadow: [BoxShadow(color: AppTheme.violet.withOpacity(.4), blurRadius: 15)]), child: IconButton(onPressed: send, icon: const Icon(Icons.arrow_upward, color: Colors.white)))])]))));

  Widget _hub() => Container(color: AppTheme.obsidian, padding: const EdgeInsets.fromLTRB(16, 25, 16, 15), child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [const Text('DOCUMENT HUB', style: TextStyle(color: Color(0xff64748b), fontSize: 11, letterSpacing: 1.5, fontWeight: FontWeight.bold)), const SizedBox(height: 13), _glassCard(Icons.memory, 'Session memory', attachmentName == null ? 'No active documents' : '$attachmentName\nStored in Memory'), const SizedBox(height: 24), const Text('QUICK EXPORT', style: TextStyle(color: Color(0xff64748b), fontSize: 11, letterSpacing: 1.5, fontWeight: FontWeight.bold)), const SizedBox(height: 12), Wrap(spacing: 8, runSpacing: 8, children: ['pdf', 'docx', 'xlsx', 'csv'].map((format) => InkWell(onTap: () => export(format), child: Container(width: 105, padding: const EdgeInsets.all(13), decoration: BoxDecoration(color: AppTheme.onyx, borderRadius: BorderRadius.circular(13), border: Border.all(color: AppTheme.border)), child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [Icon(Icons.file_present_outlined, color: format == 'pdf' ? Colors.redAccent : AppTheme.cyan), const SizedBox(height: 7), Text('.$format', style: const TextStyle(fontWeight: FontWeight.w700)), const Text('Export', style: TextStyle(color: Color(0xff64748b), fontSize: 11))])))).toList()), const Spacer(), if (context.read<AuthStore>().user?['is_admin'] == true) _gradientButton(Icons.admin_panel_settings, 'Admin console', () => Navigator.push(context, MaterialPageRoute(builder: (_) => const AdminScreen())))]));
  Widget _glassCard(IconData icon, String title, String body) => Container(width: double.infinity, padding: const EdgeInsets.all(14), decoration: BoxDecoration(color: AppTheme.onyx, borderRadius: BorderRadius.circular(15), border: Border.all(color: AppTheme.border)), child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [Icon(icon, color: AppTheme.cyan, size: 19), const SizedBox(width: 10), Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [Text(title, style: const TextStyle(fontWeight: FontWeight.w700)), const SizedBox(height: 5), Text(body, style: const TextStyle(color: Color(0xff94a3b8), fontSize: 12, height: 1.4))]))]));
  Widget _gradientButton(IconData icon, String label, VoidCallback onTap) => DecoratedBox(decoration: BoxDecoration(borderRadius: BorderRadius.circular(12), gradient: const LinearGradient(colors: [AppTheme.violet, AppTheme.indigo]), boxShadow: [BoxShadow(color: AppTheme.violet.withOpacity(.25), blurRadius: 18)]), child: ElevatedButton.icon(style: ElevatedButton.styleFrom(backgroundColor: Colors.transparent, shadowColor: Colors.transparent, foregroundColor: Colors.white, padding: const EdgeInsets.symmetric(vertical: 14)), onPressed: onTap, icon: Icon(icon, size: 18), label: Text(label)));
}

class CameraScreen extends StatefulWidget {
  final CameraDescription camera;
  const CameraScreen({required this.camera, super.key});
  @override
  State<CameraScreen> createState() => _CameraScreenState();
}
class _CameraScreenState extends State<CameraScreen> {
  late final CameraController controller;
  @override
  void initState() { super.initState(); controller = CameraController(widget.camera, ResolutionPreset.medium); controller.initialize().then((_) { if (mounted) setState(() {}); }); }
  @override
  void dispose() { controller.dispose(); super.dispose(); }
  @override
  Widget build(BuildContext context) => Scaffold(backgroundColor: AppTheme.obsidian, appBar: AppBar(title: const Text('Document scan')), body: controller.value.isInitialized ? CameraPreview(controller) : const Center(child: CircularProgressIndicator()));
}
