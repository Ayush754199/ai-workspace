import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../services/api_service.dart';
import '../services/auth_store.dart';

class VideoStudioScreen extends StatefulWidget {
  const VideoStudioScreen({super.key});

  @override
  State<VideoStudioScreen> createState() => _VideoStudioScreenState();
}

class _VideoStudioScreenState extends State<VideoStudioScreen> {
  final prompt = TextEditingController();
  Map<String, dynamic>? capabilities;
  List<Map<String, dynamic>> jobs = [];
  String? error;
  bool loading = true;
  bool generating = false;

  ApiService get api => context.read<ApiService>();
  AuthStore get auth => context.read<AuthStore>();

  @override
  void initState() {
    super.initState();
    _refresh();
  }

  @override
  void dispose() {
    prompt.dispose();
    super.dispose();
  }

  Future<void> _refresh() async {
    try {
      final result = await Future.wait(
          [api.videoCapabilities(auth.token!), api.videoJobs(auth.token!)]);
      capabilities = result[0] as Map<String, dynamic>;
      jobs = (result[1] as List)
          .whereType<Map>()
          .map((item) => Map<String, dynamic>.from(item))
          .toList();
      error = null;
    } catch (exception) {
      error = exception.toString().replaceFirst('Exception: ', '');
    }
    if (mounted) setState(() => loading = false);
  }

  Future<void> _generate() async {
    final text = prompt.text.trim();
    if (text.isEmpty || generating) return;
    setState(() {
      generating = true;
      error = null;
    });
    try {
      final job = await api.generateVideo(auth.token!, text);
      jobs = [job, ...jobs];
      prompt.clear();
    } catch (exception) {
      error = exception.toString().replaceFirst('Exception: ', '');
    }
    if (mounted) setState(() => generating = false);
  }

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(title: const Text('Video Studio'), actions: [
          IconButton(onPressed: _refresh, icon: const Icon(Icons.refresh))
        ]),
        body: loading
            ? const Center(child: CircularProgressIndicator())
            : RefreshIndicator(
                onRefresh: _refresh,
                child: ListView(padding: const EdgeInsets.all(16), children: [
                  if (error != null) _error(),
                  _capabilityCard(),
                  const SizedBox(height: 16),
                  TextField(
                      controller: prompt,
                      minLines: 3,
                      maxLines: 6,
                      decoration: const InputDecoration(
                          labelText: 'Describe your video',
                          hintText: 'A cinematic shot of a city at sunrise…')),
                  const SizedBox(height: 12),
                  FilledButton.icon(
                      onPressed: generating ? null : _generate,
                      icon: generating
                          ? const SizedBox(
                              width: 16,
                              height: 16,
                              child: CircularProgressIndicator(strokeWidth: 2))
                          : const Icon(Icons.movie_creation_outlined),
                      label:
                          Text(generating ? 'Generating…' : 'Generate video')),
                  const SizedBox(height: 24),
                  const Text('Generation history',
                      style:
                          TextStyle(fontSize: 18, fontWeight: FontWeight.w700)),
                  const SizedBox(height: 8),
                  if (jobs.isEmpty)
                    const Padding(
                        padding: EdgeInsets.symmetric(vertical: 30),
                        child: Center(child: Text('No video jobs yet.'))),
                  ...jobs.map(_jobCard),
                ]),
              ),
      );

  Widget _capabilityCard() {
    final enabled = capabilities?['feature_enabled'] == true &&
        capabilities?['user_permitted'] == true;
    return Card(
        child: ListTile(
            leading: Icon(enabled ? Icons.check_circle : Icons.info_outline,
                color: enabled ? Colors.green : Colors.orange),
            title: Text(enabled
                ? 'Video generation available'
                : 'Video generation unavailable'),
            subtitle: Text(enabled
                ? 'Text-to-video capability is enabled for this account.'
                : 'The backend currently restricts this feature.')));
  }

  Widget _jobCard(Map<String, dynamic> job) => Card(
      child: ListTile(
          leading: const Icon(Icons.video_library_outlined),
          title: Text(job['prompt']?.toString() ?? 'Untitled job',
              maxLines: 2, overflow: TextOverflow.ellipsis),
          subtitle: Text(
              '${job['status'] ?? 'unknown'} • ${job['mode'] ?? 'text'}')));

  Widget _error() => Container(
      padding: const EdgeInsets.all(12),
      margin: const EdgeInsets.only(bottom: 12),
      decoration: BoxDecoration(
          color: Colors.red.withOpacity(.12),
          borderRadius: BorderRadius.circular(12)),
      child: Text(error!));
}
