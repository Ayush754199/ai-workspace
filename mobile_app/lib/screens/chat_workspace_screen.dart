import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:file_picker/file_picker.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import 'package:provider/provider.dart';
import 'package:dio/dio.dart';
import 'package:url_launcher/url_launcher.dart';

import '../services/api_service.dart';
import '../services/auth_store.dart';
import '../theme/app_theme.dart';
import '../widgets/brand_logo.dart';
import '../widgets/nexora_motion.dart';
import 'admin_screen.dart';
import 'calls_screen.dart';
import 'image_studio_screen.dart';
import 'rooms_screen.dart';

class ChatWorkspaceScreen extends StatefulWidget {
  const ChatWorkspaceScreen({super.key});

  @override
  State<ChatWorkspaceScreen> createState() => _ChatWorkspaceScreenState();
}

class _ChatWorkspaceScreenState extends State<ChatWorkspaceScreen> {
  final composer = TextEditingController();
  final scrollController = ScrollController();
  List<Map<String, dynamic>> conversations = [];
  List<Map<String, dynamic>> messages = [];
  String? selectedId;
  String? error;
  bool loading = true;
  bool sending = false;
  bool uploading = false;
  String? attachmentName;
  final List<String> attachmentIds = [];
  CancelToken? streamCancelToken;

  ApiService get api => context.read<ApiService>();
  AuthStore get auth => context.read<AuthStore>();
  bool get _isAdmin =>
      auth.user?['is_admin'] == true ||
      auth.user?['role']?.toString().toLowerCase() == 'admin';

  @override
  void initState() {
    super.initState();
    _loadConversations();
  }

  @override
  void dispose() {
    composer.dispose();
    scrollController.dispose();
    super.dispose();
  }

  Future<void> _loadConversations() async {
    try {
      final result = await api.conversations(auth.token!);
      conversations = result
          .whereType<Map>()
          .map((item) => Map<String, dynamic>.from(item))
          .toList();
      if (conversations.isNotEmpty)
        await _openConversation(_stringId(conversations.first['id']));
    } catch (exception) {
      error = _messageFor(exception);
    }
    if (mounted) setState(() => loading = false);
  }

  Future<void> _openConversation(String id) async {
    setState(() {
      selectedId = id;
      error = null;
    });
    try {
      final result = await api.conversation(auth.token!, id);
      final rawMessages = result['messages'];
      messages = rawMessages is List
          ? rawMessages
              .whereType<Map>()
              .map((item) => Map<String, dynamic>.from(item))
              .toList()
          : [];
    } catch (exception) {
      error = _messageFor(exception);
    }
    if (mounted) {
      setState(() {});
      _scrollToEnd();
    }
  }

  Future<void> _newConversation() async {
    try {
      final result = await api.createConversation(auth.token!);
      final created = Map<String, dynamic>.from(result);
      conversations = [created, ...conversations];
      messages = [];
      selectedId = _stringId(created['id']);
      error = null;
      if (mounted) setState(() {});
    } catch (exception) {
      setState(() => error = _messageFor(exception));
    }
  }

  Future<void> _pickAttachment() async {
    if (selectedId == null || uploading) return;
    final files = await FilePicker.pickFiles();
    final path = files.isEmpty ? null : files.single.path;
    if (path == null) return;
    setState(() => uploading = true);
    try {
      final uploaded = await api.uploadAttachment(
        auth.token!,
        path,
        conversationId: selectedId,
      );
      attachmentIds.add(uploaded['id'].toString());
      attachmentName = uploaded['filename']?.toString();
    } catch (exception) {
      error = _messageFor(exception);
    }
    if (mounted) setState(() => uploading = false);
  }

  Future<void> _send() async {
    final content = composer.text.trim();
    if (content.isEmpty || selectedId == null || sending) return;
    composer.clear();
    setState(() {
      sending = true;
      error = null;
    });
    try {
      messages.add({'role': 'user', 'content': content});
      messages.add({'role': 'assistant', 'content': ''});
      final assistantIndex = messages.length - 1;
      final format = _requestedFileFormat(content);
      if (format != null) {
        final generated = await api.generateChatDocument(auth.token!, selectedId!, content, format);
        messages[assistantIndex] = {'role': 'assistant', 'content': 'Your ${format.toUpperCase()} file is ready.', 'generatedFile': generated};
        attachmentIds.clear();
        attachmentName = null;
        sending = false;
        if (mounted) setState(() {});
        return;
      }
      streamCancelToken = CancelToken();
      await for (final event in api.streamChatMessage(
        auth.token!,
        selectedId!,
        content,
        attachmentIds: List<String>.from(attachmentIds),
        cancelToken: streamCancelToken,
      )) {
        if (event['error'] != null) throw Exception(event['error']);
        if (event['chunk'] != null) {
          messages[assistantIndex]['content'] =
              '${messages[assistantIndex]['content']}${event['chunk']}';
          if (mounted) setState(() {});
          _scrollToEnd();
        }
        if (event['done'] == true && event['message'] is Map) {
          messages[assistantIndex] =
              Map<String, dynamic>.from(event['message']);
        }
      }
      attachmentIds.clear();
      attachmentName = null;
    } catch (exception) {
      if (exception is DioException && CancelToken.isCancel(exception)) {
        final index = messages.length - 1;
        if (index >= 0 && messages[index]['role'] == 'assistant') {
          messages[index] = {...messages[index], 'status': 'stopped', 'originalPrompt': content};
        }
      } else {
        error = _messageFor(exception);
      }
    }
    streamCancelToken = null;
    if (mounted) {
      setState(() => sending = false);
      _scrollToEnd();
    }
  }

  void _stop() => streamCancelToken?.cancel('Stopped by user');

  void _editPrompt(String value) {
    composer.text = value;
    composer.selection = TextSelection.collapsed(offset: value.length);
    setState(() {});
  }

  String? _requestedFileFormat(String value) {
    final text = value.toLowerCase();
    if (!RegExp(r'\b(create|generate|make|prepare|build|download|export|save|बना|बनाओ|बनाएं|तैयार|डाउनलोड)\b').hasMatch(text)) return null;
    if (RegExp(r'\b(excel|xlsx|spreadsheet|स्प्रेडशीट)\b').hasMatch(text)) return 'xlsx';
    if (RegExp(r'\b(word|docx|document|डॉक्यूमेंट|दस्तावेज़)\b').hasMatch(text)) return 'docx';
    if (RegExp(r'\b(powerpoint|pptx|presentation|प्रेजेंटेशन)\b').hasMatch(text)) return 'pptx';
    if (RegExp(r'\b(csv|comma[- ]separated)\b').hasMatch(text)) return 'csv';
    if (RegExp(r'\b(pdf|report|रिपोर्ट)\b').hasMatch(text)) return 'pdf';
    return null;
  }

  Future<void> _downloadGenerated(Map<String, dynamic> file) async {
    final bytes = base64Decode(file['data_base64']?.toString() ?? '');
    final uri = Uri.dataFromBytes(bytes, mimeType: file['content_type']?.toString() ?? 'application/octet-stream');
    await launchUrl(uri, mode: LaunchMode.externalApplication);
  }

  String _stringId(dynamic value) => value?.toString() ?? '';

  String _messageFor(Object exception) {
    final text = exception.toString();
    if (text.contains('429'))
      return 'Usage limit reached. Please try again later.';
    if (text.contains('401'))
      return 'Your session expired. Please sign in again.';
    if (text.contains('503'))
      return 'The AI provider is temporarily unavailable.';
    return text.replaceFirst('Exception: ', '');
  }

  void _scrollToEnd() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (scrollController.hasClients) {
        scrollController.animateTo(
          scrollController.position.maxScrollExtent,
          duration: const Duration(milliseconds: 250),
          curve: Curves.easeOut,
        );
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      drawer: _drawer(),
      appBar: AppBar(
        title: Text(_title, maxLines: 1, overflow: TextOverflow.ellipsis),
        leading: Builder(
          builder: (context) => IconButton(
            icon: const Icon(Icons.menu_rounded),
            onPressed: () => Scaffold.of(context).openDrawer(),
          ),
        ),
        actions: [
          IconButton(
            tooltip: 'New chat',
            onPressed: _newConversation,
            icon: const Icon(Icons.add_comment_outlined),
          ),
        ],
      ),
      body: Column(
        children: [
          if (error != null) _errorBanner(),
          Expanded(
            child: loading
                ? const Center(child: NexoraMotion(size: 120))
                : _messageList(),
          ),
          _composer(),
        ],
      ),
    );
  }

  String get _title {
    Map<String, dynamic>? item;
    for (final conversation in conversations) {
      if (_stringId(conversation['id']) == selectedId) {
        item = conversation;
        break;
      }
    }
    return item?['title']?.toString() ?? 'New chat';
  }

  Widget _drawer() => Drawer(
        child: SafeArea(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              const Padding(padding: EdgeInsets.all(20), child: BrandLogo()),
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 16),
                child: FilledButton.icon(
                  onPressed: _newConversation,
                  icon: const Icon(Icons.add),
                  label: const Text('New chat'),
                ),
              ),
              const Padding(
                padding: EdgeInsets.fromLTRB(20, 24, 20, 8),
                child: Text(
                  'CONVERSATIONS',
                  style: TextStyle(
                    fontSize: 11,
                    letterSpacing: 1.4,
                    color: Colors.grey,
                  ),
                ),
              ),
              Expanded(
                child: ListView.builder(
                  itemCount: conversations.length,
                  itemBuilder: (context, index) {
                    final item = conversations[index];
                    final id = _stringId(item['id']);
                    return ListTile(
                      selected: id == selectedId,
                      leading: const Icon(Icons.chat_bubble_outline),
                      title: Text(
                        item['title']?.toString() ?? 'Untitled chat',
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                      ),
                      onTap: () {
                        Navigator.pop(context);
                        _openConversation(id);
                      },
                    );
                  },
                ),
              ),
              ListTile(
                leading: const Icon(Icons.auto_awesome_outlined),
                title: const Text('Image studio'),
                onTap: () => Navigator.push(
                    context,
                    MaterialPageRoute(
                        builder: (_) => const ImageStudioScreen())),
              ),
              ListTile(
                leading: const Icon(Icons.groups_2_outlined),
                title: const Text('Chat rooms'),
                onTap: () => Navigator.push(context,
                    MaterialPageRoute(builder: (_) => const RoomsScreen())),
              ),
              ListTile(
                leading: const Icon(Icons.call_outlined),
                title: const Text('Calls'),
                onTap: () => Navigator.push(context,
                    MaterialPageRoute(builder: (_) => const CallsScreen())),
              ),
              if (_isAdmin)
                ListTile(
                  leading: const Icon(Icons.admin_panel_settings_outlined),
                  title: const Text('Admin console'),
                  onTap: () => Navigator.push(context,
                      MaterialPageRoute(builder: (_) => const AdminScreen())),
                ),
              ListTile(
                leading: const Icon(Icons.logout),
                title: const Text('Sign out'),
                onTap: () => auth.logout(),
              ),
            ],
          ),
        ),
      );

  Widget _messageList() {
    if (selectedId == null) return _emptyState('Start a new chat to begin.');
    if (messages.isEmpty) return _emptyState('Ask Nexora anything.');
    return ListView.builder(
      controller: scrollController,
      padding: const EdgeInsets.fromLTRB(16, 20, 16, 20),
      itemCount: messages.length,
      itemBuilder: (context, index) => _messageBubble(messages[index]),
    );
  }

  Widget _emptyState(String message) => Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const NexoraMotion(size: 132),
            const SizedBox(height: 12),
            Text(message, style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 6),
            const Text('Your ideas, amplified.',
                style: TextStyle(color: Colors.grey)),
          ],
        ),
      );

  Widget _messageBubble(Map<String, dynamic> message) {
    final role = message['role']?.toString() ??
        (message['sender_id'] == auth.user?['id'] ? 'user' : 'assistant');
    final own = role == 'user';
    return Align(
      alignment: own ? Alignment.centerRight : Alignment.centerLeft,
      child: Container(
        constraints: const BoxConstraints(maxWidth: 760),
        margin: const EdgeInsets.only(bottom: 14),
        padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(
          color: own
              ? AppTheme.violet.withOpacity(.28)
              : Theme.of(context).colorScheme.surface,
          borderRadius: BorderRadius.circular(16),
          border: Border.all(
            color: own ? AppTheme.violet.withOpacity(.6) : AppTheme.border,
          ),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              own ? 'You' : 'Nexora AI',
              style: TextStyle(
                fontSize: 11,
                color: own ? AppTheme.cyan : Colors.grey,
                fontWeight: FontWeight.w700,
              ),
            ),
            const SizedBox(height: 5),
            if (!own &&
                (message['content']?.toString() ?? '').isEmpty &&
                sending)
              const NexoraTyping()
            else
              Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                MarkdownBody(data: message['content']?.toString() ?? ''),
                if (message['status'] == 'stopped' && message['originalPrompt'] is String)
                  TextButton.icon(
                    onPressed: () => _editPrompt(message['originalPrompt'] as String),
                    icon: const Icon(Icons.edit_outlined),
                    label: const Text('Edit prompt'),
                  ),
                if (message['generatedFile'] is Map)
                  TextButton.icon(
                    onPressed: () => _downloadGenerated(Map<String, dynamic>.from(message['generatedFile'] as Map)),
                    icon: const Icon(Icons.download_outlined),
                    label: Text('Download ${message['generatedFile']['filename'] ?? 'file'}'),
                  ),
              ]),
          ],
        ),
      ),
    );
  }

  Widget _composer() => SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(12, 8, 12, 12),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              Expanded(
                child: TextField(
                  controller: composer,
                  minLines: 1,
                  maxLines: 5,
                  textInputAction: TextInputAction.newline,
                  onSubmitted: (_) => _send(),
                  decoration: InputDecoration(
                    hintText:
                        uploading ? 'Uploading attachment…' : 'Message Nexora…',
                    prefixIcon: IconButton(
                      tooltip: 'Attach file',
                      onPressed: uploading ? null : _pickAttachment,
                      icon: Icon(uploading
                          ? Icons.cloud_upload_outlined
                          : Icons.attach_file),
                    ),
                    suffixIcon: attachmentName == null
                        ? null
                        : Padding(
                            padding: const EdgeInsets.all(8),
                            child: Chip(
                                label: Text(attachmentName!,
                                    overflow: TextOverflow.ellipsis))),
                    filled: true,
                  ),
                ),
              ),
              const SizedBox(width: 8),
              IconButton.filled(
                onPressed: sending ? _stop : _send,
                icon: sending ? const Icon(Icons.stop_rounded) : const Icon(Icons.arrow_upward),
              ),
            ],
          ),
        ),
      );

  Widget _errorBanner() => MaterialBanner(
        content: Text(error!),
        leading: const Icon(Icons.warning_amber_rounded),
        actions: [
          TextButton(
            onPressed: () => setState(() => error = null),
            child: const Text('Dismiss'),
          ),
        ],
      );
}
