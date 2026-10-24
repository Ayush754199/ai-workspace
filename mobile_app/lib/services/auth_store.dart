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
