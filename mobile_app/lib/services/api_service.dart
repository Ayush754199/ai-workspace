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
