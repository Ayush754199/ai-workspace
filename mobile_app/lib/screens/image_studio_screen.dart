import 'dart:typed_data';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../services/api_service.dart';
import '../services/auth_store.dart';
import '../widgets/nexora_motion.dart';

class ImageStudioScreen extends StatefulWidget {
  const ImageStudioScreen({super.key});
  @override
  State<ImageStudioScreen> createState() => _ImageStudioScreenState();
}

class _ImageStudioScreenState extends State<ImageStudioScreen> {
  final prompt = TextEditingController();
  Uint8List? image;
  String? sourcePath;
  String? error;
  bool generating = false;

  @override
  void dispose() {
    prompt.dispose();
    super.dispose();
  }

  Future<void> _pickSource() async {
    final files = await FilePicker.pickFiles(type: FileType.image);
    if (files.isNotEmpty && mounted)
      setState(() => sourcePath = files.single.path);
  }

  Future<void> _generate() async {
    if (prompt.text.trim().isEmpty || generating) return;
    setState(() {
      generating = true;
      error = null;
      image = null;
    });
    try {
      image = await context.read<ApiService>().generateImage(
            context.read<AuthStore>().token!,
            prompt.text.trim(),
            imagePath: sourcePath,
          );
    } catch (exception) {
      error = exception.toString().replaceFirst('Exception: ', '');
    }
    if (mounted) setState(() => generating = false);
  }

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(title: const Text('Image studio')),
        body: ListView(
          padding: const EdgeInsets.all(18),
          children: [
            const Text('Imagine it. Nexora creates it.',
                style: TextStyle(fontSize: 22, fontWeight: FontWeight.w700)),
            const SizedBox(height: 6),
            Text(
                'Generate a new image or describe an edit for an uploaded image.',
                style: Theme.of(context).textTheme.bodyMedium),
            const SizedBox(height: 20),
            TextField(
                controller: prompt,
                minLines: 3,
                maxLines: 6,
                decoration: const InputDecoration(
                    hintText:
                        'A cinematic floating library above the clouds…')),
            const SizedBox(height: 12),
            OutlinedButton.icon(
                onPressed: generating ? null : _pickSource,
                icon: const Icon(Icons.add_photo_alternate_outlined),
                label: Text(sourcePath == null
                    ? 'Add image to edit (optional)'
                    : 'Source image selected')),
            const SizedBox(height: 12),
            FilledButton.icon(
                onPressed: generating ? null : _generate,
                icon: const Icon(Icons.auto_awesome),
                label: Text(generating ? 'Creating image…' : 'Create image')),
            const SizedBox(height: 24),
            if (generating)
              const Center(
                  child: Column(children: [
                NexoraMotion(size: 170),
                SizedBox(height: 8),
                Text('Turning your words into pixels…')
              ])),
            if (error != null)
              Text(error!, style: const TextStyle(color: Colors.red)),
            if (image != null)
              ClipRRect(
                  borderRadius: BorderRadius.circular(20),
                  child: Image.memory(image!, fit: BoxFit.cover)),
          ],
        ),
      );
}
