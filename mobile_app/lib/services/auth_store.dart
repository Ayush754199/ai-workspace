import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:dio/dio.dart';
import 'dart:convert';

import 'api_service.dart';

class AuthStore extends ChangeNotifier {
  final _storage = const FlutterSecureStorage();
  String? token;
  Map<String, dynamic>? user;

  bool get isAuthenticated => token != null && user != null;

  Future<void> restore(ApiService api) async {
    final savedToken = await _storage.read(key: 'auth_token');
    final savedUser = await _storage.read(key: 'auth_user');
    if (savedToken == null || savedToken.isEmpty) return;
    if (savedUser != null) {
      try { user = Map<String, dynamic>.from(jsonDecode(savedUser) as Map); } catch (_) {}
    }
    try {
      final profile = await api.me(savedToken);
      token = savedToken;
      user = profile;
      notifyListeners();
    } catch (exception) {
      // Returning from the Android home screen can briefly lose connectivity.
      // Keep the secure token unless the API explicitly rejected it.
      if (exception is! DioException || exception.response?.statusCode == 401) {
        await _storage.delete(key: 'auth_token');
      } else {
        token = savedToken;
        notifyListeners();
      }
    }
  }

  Future<void> login(String accessToken, Map<String, dynamic> profile) async {
    token = accessToken;
    user = profile;
    await _storage.write(key: 'auth_token', value: accessToken);
    await _storage.write(key: 'auth_user', value: jsonEncode(profile));
    notifyListeners();
  }

  Future<void> logout() async {
    token = null;
    user = null;
    await _storage.delete(key: 'auth_token');
    await _storage.delete(key: 'auth_user');
    notifyListeners();
  }
}
