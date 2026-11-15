# Mobile App — Complete Dependencies & Code Reference

**Project:** AI Workspace v1.0  
**App:** NEXUS AI Workspace (Flutter)  
**Path:** `mobile_app/`  
**Platforms:** Android + iOS  
**Status:** Reference only — no existing source files were modified  

---

## 1. pubspec.yaml — Complete Dependencies

```yaml
name: ai_workspace_mobile
description: AI Workspace mobile client
publish_to: "none"
version: 1.0.0+1

environment:
  sdk: ">=3.3.0 <4.0.0"

dependencies:
  flutter:
    sdk: flutter
  cupertino_icons: ^1.0.8
  dio: ^5.7.0
  provider: ^6.1.2
  flutter_secure_storage: ^9.2.2
  local_auth: ^2.3.0
  speech_to_text: ^7.0.0
  flutter_tts: ^4.2.0
  camera: ^0.11.0+2
  file_picker: ^8.1.4
  flutter_markdown: ^0.7.4+1
  google_fonts: ^6.2.1
  image_picker: ^1.1.2
  http: ^1.2.2

dev_dependencies:
  flutter_test:
    sdk: flutter
  flutter_lints: ^5.0.0

flutter:
  uses-material-design: true
```

### Dependency Purpose Summary

| Package | Version | Purpose |
|---------|---------|---------|
| `flutter` | sdk | UI framework |
| `cupertino_icons` | ^1.0.8 | iOS-style icons |
| `dio` | ^5.7.0 | HTTP client for backend API calls |
| `provider` | ^6.1.2 | State management (AuthStore, ApiService) |
| `flutter_secure_storage` | ^9.2.2 | Encrypted token storage |
| `local_auth` | ^2.3.0 | Biometric unlock (fingerprint/face) |
| `speech_to_text` | ^7.0.0 | Voice input / STT |
| `flutter_tts` | ^4.2.0 | Text-to-speech playback |
| `camera` | ^0.11.0+2 | Camera preview & capture |
| `file_picker` | ^8.1.4 | Document file selection (PDF, DOCX, images) |
| `flutter_markdown` | ^0.7.4+1 | Render AI message markdown |
| `google_fonts` | ^6.2.1 | Plus Jakarta Sans typography |
| `image_picker` | ^1.1.2 | Gallery/camera image selection |
| `http` | ^1.2.2 | Included but unused (all calls use Dio) |
| `flutter_test` | sdk | Unit/widget tests |
| `flutter_lints` | ^5.0.0 | Lint rules |

---

## 2. Directory Structure

```
mobile_app/
├── pubspec.yaml
├── lib/
│   ├── main.dart                    # App entry, biometric unlock gate, providers
│   ├── services/
│   │   ├── api_service.dart         # Dio HTTP client + all backend API methods
│   │   └── auth_store.dart          # ChangeNotifier + secure token storage
│   ├── screens/
│   │   ├── auth_screen.dart         # Login / Signup tabs + DOB picker
│   │   ├── home_screen.dart         # Rooms, chat, voice, attachments, export, document hub
│   │   └── admin_screen.dart        # Admin chat surveillance + audit CSV export
│   ├── theme/
│   │   └── app_theme.dart           # Dark theme (obsidian/onyx/violet/cyan)
│   └── widgets/
│       └── brand_logo.dart          # Custom hexagon logo painter (NEXUS AI WORKSPACE)
```

---

## 3. Complete Source Code

### 3.1 `lib/main.dart`

```dart
import 'package:flutter/material.dart';
import 'package:local_auth/local_auth.dart';
import 'package:provider/provider.dart';

import 'services/api_service.dart';
import 'services/auth_store.dart';
import 'screens/auth_screen.dart';
import 'screens/home_screen.dart';
import 'theme/app_theme.dart';

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
          title: 'AI Workspace',
          theme: AppTheme.dark(),
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
    final auth = LocalAuthentication();
    try {
      unlocked = await auth.authenticate(localizedReason: 'Unlock AI Workspace');
    } catch (_) {
      unlocked = true;
    }
    if (mounted) setState(() => checking = false);
  }

  @override
  Widget build(BuildContext context) {
    if (checking) return const Scaffold(body: Center(child: CircularProgressIndicator()));
    if (!unlocked) return Scaffold(body: Center(child: FilledButton(onPressed: _unlock, child: const Text('Unlock'))));
    return Consumer<AuthStore>(builder: (_, store, __) => store.token == null ? const AuthScreen() : const HomeScreen());
  }
}
```

### 3.2 `lib/services/api_service.dart`

```dart
import 'dart:typed_data';
import 'package:dio/dio.dart';

class ApiService {
  final Dio dio = Dio(BaseOptions(baseUrl: 'http://10.0.2.2:8000'));

  Future<Map<String, dynamic>> login(String identifier, String password) async {
    final response = await dio.post('/api/v1/auth/login', data: {'identifier': identifier, 'password': password});
    final token = response.data['access_token'] as String;
    final profile = await dio.get('/api/v1/auth/me', options: Options(headers: {'Authorization': 'Bearer $token'}));
    return {'token': token, 'user': profile.data};
  }

  Future<List<dynamic>> rooms(String token) async => (await dio.get('/api/v1/rooms', options: _auth(token))).data;
  Future<List<dynamic>> messages(String token, int roomId) async => (await dio.get('/api/v1/rooms/$roomId/messages', options: _auth(token))).data;
  Future<dynamic> sendMessage(String token, int roomId, String content) async => (await dio.post('/api/v1/rooms/$roomId/messages', data: {'content': content}, options: _auth(token))).data;
  Future<dynamic> summary(String token, int roomId) async => (await dio.post('/api/v1/rooms/$roomId/summary', options: _auth(token))).data;
  Future<Uint8List> exportDocument(String token, String format, String content) async => Uint8List.fromList((await dio.post('/api/v1/chat/export', data: {'format': format, 'content': content, 'filename': 'nexus_export'}, options: Options(responseType: ResponseType.bytes, headers: {'Authorization': 'Bearer $token'}))).data);
  Future<Uint8List> exportAudit(String token) async => Uint8List.fromList((await dio.get('/api/v1/admin/audit/export', options: Options(responseType: ResponseType.bytes, headers: {'Authorization': 'Bearer $token'}))).data);
  Future<List<dynamic>> adminChats(String token) async => (await dio.get('/api/v1/admin/chats', options: _auth(token))).data['items'];
  Future<dynamic> adminTranscript(String token, int roomId) async => (await dio.get('/api/v1/admin/chats/$roomId', options: _auth(token))).data;
  Future<void> register(String identifier, String password, String fullName, String dob) async => dio.post('/api/v1/auth/signup', data: {'identifier': identifier, 'password': password, 'full_name': fullName, 'dob': dob});
  Options _auth(String token) => Options(headers: {'Authorization': 'Bearer $token'});
}
```

### 3.3 `lib/services/auth_store.dart`

```dart
import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

class AuthStore extends ChangeNotifier {
  final _storage = const FlutterSecureStorage();
  String? token;
  Map<String, dynamic>? user;

  Future<void> login(String accessToken, Map<String, dynamic> profile) async {
    token = accessToken;
    user = profile;
    await _storage.write(key: 'auth_token', value: accessToken);
    notifyListeners();
  }

  Future<void> logout() async {
    token = null;
    user = null;
    await _storage.delete(key: 'auth_token');
    notifyListeners();
  }
}
```

### 3.4 `lib/screens/auth_screen.dart`

```dart
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../services/api_service.dart';
import '../services/auth_store.dart';

class AuthScreen extends StatefulWidget {
  const AuthScreen({super.key});
  @override
  State<AuthScreen> createState() => _AuthScreenState();
}

class _AuthScreenState extends State<AuthScreen> with SingleTickerProviderStateMixin {
  late final TabController tabs;
  final identifier = TextEditingController();
  final password = TextEditingController();
  final fullName = TextEditingController();
  DateTime? dob;
  bool loading = false;
  String error = '';

  @override
  void initState() { super.initState(); tabs = TabController(length: 2, vsync: this); }
  @override
  void dispose() { tabs.dispose(); identifier.dispose(); password.dispose(); fullName.dispose(); super.dispose(); }

  Future<void> pickDob() async {
    final selected = await showDatePicker(context: context, firstDate: DateTime(1900), lastDate: DateTime.now(), initialDate: DateTime(2000));
    if (selected != null) setState(() => dob = selected);
  }

  Future<void> submit() async {
    setState(() { loading = true; error = ''; });
    try {
      final api = context.read<ApiService>();
      if (tabs.index == 1) {
        if (dob == null) throw Exception('Please select your date of birth');
        await api.register(identifier.text.trim(), password.text, fullName.text.trim(), dob!.toIso8601String().split('T').first);
      }
      final result = await api.login(identifier.text.trim(), password.text);
      await context.read<AuthStore>().login(result['token'], result['user']);
    } catch (exception) { setState(() => error = exception.toString().replaceFirst('Exception: ', '')); }
    if (mounted) setState(() => loading = false);
  }

  Widget form(bool signup) => Padding(padding: const EdgeInsets.all(24), child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
    TextField(controller: identifier, keyboardType: TextInputType.emailAddress, decoration: const InputDecoration(labelText: 'Email or mobile number')),
    if (signup) TextField(controller: fullName, decoration: const InputDecoration(labelText: 'Full name')),
    if (signup) OutlinedButton.icon(onPressed: pickDob, icon: const Icon(Icons.calendar_today), label: Text(dob == null ? 'Select date of birth' : '${dob!.day}/${dob!.month}/${dob!.year}')),
    TextField(controller: password, obscureText: true, decoration: const InputDecoration(labelText: 'Password')),
    if (error.isNotEmpty) Padding(padding: const EdgeInsets.only(top: 12), child: Text(error, style: const TextStyle(color: Colors.red))),
    const SizedBox(height: 20),
    FilledButton(onPressed: loading ? null : submit, child: Text(loading ? 'Please wait...' : signup ? 'Create Account' : 'Login')),
  ]));

  @override
  Widget build(BuildContext context) => Scaffold(appBar: AppBar(title: const Text('AI Workspace')), body: SafeArea(child: Center(child: ConstrainedBox(constraints: const BoxConstraints(maxWidth: 460), child: Column(children: [const SizedBox(height: 18), Text('Secure mobile workspace', style: Theme.of(context).textTheme.titleMedium), TabBar(controller: tabs, tabs: const [Tab(text: 'Login'), Tab(text: 'Signup')]), Expanded(child: TabBarView(controller: tabs, children: [form(false), form(true)]))]))));
}
```

### 3.5 `lib/screens/home_screen.dart`

```dart
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
```

### 3.6 `lib/screens/admin_screen.dart`

```dart
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
```

### 3.7 `lib/theme/app_theme.dart`

```dart
import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';

class AppTheme {
  static const obsidian = Color(0xff090a0f);
  static const onyx = Color(0xff13151e);
  static const border = Color(0xff26293b);
  static const violet = Color(0xff7c3aed);
  static const indigo = Color(0xff6366f1);
  static const cyan = Color(0xff06b6d4);
  static const ice = Color(0xfff8fafc);

  static ThemeData dark() => ThemeData(
        brightness: Brightness.dark,
        scaffoldBackgroundColor: obsidian,
        colorScheme: const ColorScheme.dark(primary: violet, secondary: cyan, surface: onyx),
        textTheme: GoogleFonts.plusJakartaSansTextTheme(ThemeData.dark().textTheme).apply(bodyColor: ice, displayColor: ice),
        inputDecorationTheme: InputDecorationTheme(
          filled: true,
          fillColor: onyx,
          hintStyle: TextStyle(color: ice.withOpacity(.42)),
          border: OutlineInputBorder(borderRadius: BorderRadius.circular(14), borderSide: const BorderSide(color: border)),
          enabledBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(14), borderSide: const BorderSide(color: border)),
          focusedBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(14), borderSide: const BorderSide(color: cyan)),
        ),
        appBarTheme: const AppBarTheme(backgroundColor: obsidian, elevation: 0, surfaceTintColor: Colors.transparent),
        dividerColor: border,
        useMaterial3: true,
      );
}
```

### 3.8 `lib/widgets/brand_logo.dart`

```dart
import 'package:flutter/material.dart';
import '../theme/app_theme.dart';

class BrandLogo extends StatelessWidget {
  final bool compact;
  const BrandLogo({super.key, this.compact = false});

  @override
  Widget build(BuildContext context) => Row(mainAxisSize: MainAxisSize.min, children: [
        Container(width: compact ? 34 : 42, height: compact ? 34 : 42, decoration: BoxDecoration(shape: BoxShape.circle, boxShadow: [BoxShadow(color: AppTheme.violet.withOpacity(.4), blurRadius: 22)]), child: CustomPaint(painter: _HexPainter())),
        if (!compact) ...[
          const SizedBox(width: 12),
          RichText(text: const TextSpan(style: TextStyle(fontWeight: FontWeight.w700, fontSize: 15, letterSpacing: .3), children: [TextSpan(text: 'NEXUS ', style: TextStyle(color: Colors.white)), TextSpan(text: 'AI', style: TextStyle(color: AppTheme.cyan)), TextSpan(text: ' WORKSPACE', style: TextStyle(color: Color(0xffa78bfa), fontSize: 11))])),
        ],
      ]);
}

class _HexPainter extends CustomPainter {
  @override
  void paint(Canvas canvas, Size size) {
    final rect = Offset.zero & size;
    final paint = Paint()..shader = const LinearGradient(colors: [AppTheme.violet, AppTheme.indigo]).createShader(rect);
    final path = Path()..moveTo(size.width * .5, 0)..lineTo(size.width, size.height * .25)..lineTo(size.width * .86, size.height * .8)..lineTo(size.width * .5, size.height)..lineTo(size.width * .14, size.height * .8)..lineTo(0, size.height * .25)..close();
    canvas.drawPath(path, paint);
    final inner = Paint()..color = AppTheme.obsidian;
    canvas.drawCircle(size.center(Offset.zero), size.width * .22, inner);
    canvas.drawCircle(size.center(Offset.zero), size.width * .1, Paint()..color = AppTheme.cyan);
  }

  @override
  bool shouldRepaint(covariant CustomPainter oldDelegate) => false;
}
```

---

## 4. Backend API Endpoints Consumed by Mobile

| Method | Endpoint | Used in |
|--------|----------|---------|
| POST | `/api/v1/auth/login` | `api_service.dart:login` |
| GET | `/api/v1/auth/me` | `api_service.dart:login` |
| POST | `/api/v1/auth/signup` | `api_service.dart:register` |
| GET | `/api/v1/rooms` | `api_service.dart:rooms` |
| GET | `/api/v1/rooms/{id}/messages` | `api_service.dart:messages` |
| POST | `/api/v1/rooms/{id}/messages` | `api_service.dart:sendMessage` |
| POST | `/api/v1/rooms/{id}/summary` | `api_service.dart:summary` |
| POST | `/api/v1/chat/export` | `api_service.dart:exportDocument` |
| GET | `/api/v1/admin/audit/export` | `api_service.dart:exportAudit` |
| GET | `/api/v1/admin/chats` | `api_service.dart:adminChats` |
| GET | `/api/v1/admin/chats/{id}` | `api_service.dart:adminTranscript` |

---

## 5. Notes

- All files above are **read-only references** — no existing source files were modified.
- The `http` package (`^1.2.2`) is listed in pubspec.yaml but unused; all API calls use `dio`.
- `home_screen.dart:67` has a known syntax issue: `Container constraints: ...` is missing constructor parentheses.
- Base URL is hardcoded to `http://10.0.2.2:8000` (Android emulator). For iOS simulator use `localhost:8000`; for physical devices use the machine's LAN IP.
