import 'dart:convert';
import 'dart:typed_data';

import 'package:dio/dio.dart';

class ApiService {
  static const defaultBaseUrl = String.fromEnvironment(
    'AI_WORKSPACE_API_URL',
    defaultValue: 'https://ai-workspace-gm23.onrender.com',
  );

  final Dio dio = Dio(
    BaseOptions(
      baseUrl: defaultBaseUrl,
      connectTimeout: const Duration(seconds: 15),
      receiveTimeout: const Duration(seconds: 60),
      headers: {'Accept': 'application/json'},
    ),
  );

  Future<Map<String, dynamic>> login(String identifier, String password) async {
    final response = await dio.post(
      '/api/v1/auth/login',
      data: {'identifier': identifier, 'password': password},
    );
    final token = response.data['access_token'] as String;
    final profile = await dio.get(
      '/api/v1/auth/me',
      options: Options(headers: {'Authorization': 'Bearer $token'}),
    );
    return {'token': token, 'user': profile.data};
  }

  Future<Map<String, dynamic>> me(String token) async =>
      (await dio.get('/api/v1/auth/me', options: _auth(token))).data
          as Map<String, dynamic>;

  Future<List<dynamic>> conversations(String token) async => (await dio.get(
        '/api/v1/chat/v2/conversations',
        options: _auth(token),
      ))
          .data as List<dynamic>;

  Future<Map<String, dynamic>> createConversation(
    String token, {
    String mode = 'chat',
  }) async =>
      (await dio.post(
        '/api/v1/chat/v2/conversations',
        data: {'mode': mode},
        options: _auth(token),
      ))
          .data as Map<String, dynamic>;

  Future<Map<String, dynamic>> conversation(String token, String id) async =>
      (await dio.get(
        '/api/v1/chat/v2/conversations/$id',
        options: _auth(token),
      ))
          .data as Map<String, dynamic>;

  Future<Map<String, dynamic>> generateChatDocument(
    String token,
    String id,
    String prompt,
    String format,
  ) async =>
      (await dio.post(
        '/api/v1/chat/v2/conversations/$id/documents/generate',
        data: {'prompt': prompt, 'format': format, 'conversation_id': int.tryParse(id)},
        options: _auth(token),
      )).data as Map<String, dynamic>;

  Future<Map<String, dynamic>> sendChatMessage(
    String token,
    String id,
    String content, {
    String? model,
    List<String> attachmentIds = const [],
  }) async =>
      (await dio.post(
        '/api/v1/chat/v2/conversations/$id/messages',
        data: {
          'content': content,
          if (model != null) 'model': model,
          'attachment_ids': attachmentIds,
        },
        options: _auth(token),
      ))
          .data as Map<String, dynamic>;

  Stream<Map<String, dynamic>> streamChatMessage(
    String token,
    String id,
    String content, {
    String? model,
    List<String> attachmentIds = const [],
    bool webSearch = false,
    CancelToken? cancelToken,
  }) async* {
    final response = await dio.post<ResponseBody>(
      '/api/v1/chat/v2/conversations/$id/stream',
      data: {
        'content': content,
        if (model != null) 'model': model,
        'attachment_ids': attachmentIds,
        'web_search': webSearch,
      },
      options: _auth(token).copyWith(responseType: ResponseType.stream),
      cancelToken: cancelToken,
    );
    await for (final line in response.data!.stream
        .cast<List<int>>()
        .transform(utf8.decoder)
        .transform(const LineSplitter())) {
      if (!line.startsWith('data:')) continue;
      final payload = line.substring(5).trim();
      if (payload == '[DONE]') break;
      final decoded = jsonDecode(payload);
      if (decoded is Map) yield Map<String, dynamic>.from(decoded);
    }
  }

  Future<Map<String, dynamic>> uploadAttachment(
    String token,
    String path, {
    String? conversationId,
  }) async {
    final form = FormData.fromMap({
      'file': await MultipartFile.fromFile(path),
      if (conversationId != null) 'conversation_id': conversationId,
    });
    return (await dio.post(
      '/api/v1/chat/v2/attachments',
      data: form,
      options: _auth(token),
    ))
        .data as Map<String, dynamic>;
  }

  Future<Uint8List> generateImage(
    String token,
    String prompt, {
    String? imagePath,
  }) async {
    final form = FormData.fromMap({
      'prompt': prompt,
      if (imagePath != null) 'image': await MultipartFile.fromFile(imagePath),
    });
    final response = await dio.post<List<int>>(
      '/api/v1/chat/image',
      data: form,
      options: _auth(token).copyWith(responseType: ResponseType.bytes),
    );
    return Uint8List.fromList(response.data ?? <int>[]);
  }

  Future<Map<String, dynamic>> videoCapabilities(String token) async =>
      (await dio.get('/api/v1/video/capabilities', options: _auth(token))).data
          as Map<String, dynamic>;

  Future<List<dynamic>> videoJobs(String token) async =>
      (await dio.get('/api/v1/video/jobs', options: _auth(token))).data
          as List<dynamic>;

  Future<Map<String, dynamic>> generateVideo(
          String token, String prompt) async =>
      (await dio.post('/api/v1/video/generate',
              data: {'prompt': prompt}, options: _auth(token)))
          .data as Map<String, dynamic>;

  Future<List<dynamic>> calls(String token) async =>
      (await dio.get('/api/v1/calls', options: _auth(token))).data
          as List<dynamic>;

  Future<Map<String, dynamic>> startCall(String token, int participantId,
          {String type = 'audio'}) async =>
      (await dio.post('/api/v1/calls',
              data: {
                'participant_ids': [participantId],
                'call_type': type,
                'idempotency_key':
                    DateTime.now().microsecondsSinceEpoch.toString()
              },
              options: _auth(token)))
          .data as Map<String, dynamic>;

  Future<Map<String, dynamic>> callAction(
          String token, String callId, String action) async =>
      (await dio.post('/api/v1/calls/$callId/$action', options: _auth(token)))
          .data as Map<String, dynamic>;

  Future<List<dynamic>> rooms(String token) async =>
      (await dio.get('/api/v1/rooms', options: _auth(token))).data;
  Future<List<dynamic>> messages(String token, int roomId) async =>
      (await dio.get(
        '/api/v1/rooms/$roomId/messages',
        options: _auth(token),
      ))
          .data;
  Future<dynamic> sendMessage(String token, int roomId, String content) async =>
      (await dio.post(
        '/api/v1/rooms/$roomId/messages',
        data: {'content': content},
        options: _auth(token),
      ))
          .data;
  Future<dynamic> summary(String token, int roomId) async => (await dio.post(
        '/api/v1/rooms/$roomId/summary',
        options: _auth(token),
      ))
          .data;
  Future<Uint8List> exportDocument(
    String token,
    String format,
    String content,
  ) async =>
      Uint8List.fromList(
        (await dio.post(
          '/api/v1/chat/export',
          data: {
            'format': format,
            'content': content,
            'filename': 'nexus_export'
          },
          options: Options(
            responseType: ResponseType.bytes,
            headers: {'Authorization': 'Bearer $token'},
          ),
        ))
            .data,
      );
  Future<Uint8List> exportAudit(String token) async => Uint8List.fromList(
        (await dio.get(
          '/api/v1/admin/audit/export',
          options: Options(
            responseType: ResponseType.bytes,
            headers: {'Authorization': 'Bearer $token'},
          ),
        ))
            .data,
      );
  Future<List<dynamic>> adminChats(String token) async => (await dio.get(
        '/api/v1/admin/chats',
        options: _auth(token),
      ))
          .data['items'];
  Future<Map<String, dynamic>> adminAnalytics(String token) async =>
      (await dio.get('/api/v1/admin/analytics', options: _auth(token))).data
          as Map<String, dynamic>;
  Future<List<dynamic>> adminUsers(String token) async =>
      (await dio.get('/api/v1/admin/users', options: _auth(token))).data
          as List<dynamic>;
  Future<Map<String, dynamic>> adminSettings(String token) async =>
      (await dio.get('/api/v1/admin/settings', options: _auth(token))).data
          as Map<String, dynamic>;
  Future<dynamic> adminTranscript(String token, int roomId) async =>
      (await dio.get(
        '/api/v1/admin/chats/$roomId',
        options: _auth(token),
      ))
          .data;
  Future<void> register(
    String identifier,
    String password,
    String fullName,
    String dob,
  ) async =>
      dio.post(
        '/api/v1/auth/signup',
        data: {
          'identifier': identifier,
          'password': password,
          'full_name': fullName,
          'dob': dob,
        },
      );
  Options _auth(String token) =>
      Options(headers: {'Authorization': 'Bearer $token'});
}
