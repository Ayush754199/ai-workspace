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
