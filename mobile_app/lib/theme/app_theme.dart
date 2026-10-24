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