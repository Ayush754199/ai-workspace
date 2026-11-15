# Mobile Test Report

Date: 2026-11-12  
Branch: `feature/flutter-mobile-app`

## Completed evidence

- Live health endpoint: passed (`200`, `{"status":"ok"}`).
- Admin login: passed (`200`, token response; token not persisted in audit output).
- User login: passed (`200`, token response; token not persisted in audit output).
- Admin read-only probes: profile, models, conversations, attachments, rooms, video, voice, calls and admin endpoints returned `200`.
- User read-only probes: profile, models, conversations, attachments, rooms, video, voice and calls returned `200`.
- User authorization boundary: admin users, analytics and provider-model endpoints returned `403`.
- Dart formatting: passed for the changed mobile Dart files.
- Flutter project generation: Android and iOS scaffolds generated successfully.
- Flutter dependency resolution: passed (`flutter pub get`).
- Flutter widget test: passed (`AI Workspace boots`).
- Flutter static analysis: no compile errors; 18 non-fatal style/deprecation infos remain.
- Chat SSE client and multipart upload code: static analysis passed.
- Video capabilities/jobs/generate client and call lifecycle screens: static analysis passed.
- Android device detected: `CPH2371` / Android 13 / `U8OJ75WW7DT8YDSW`.
- Physical-device install attempt: blocked before APK creation because the local Android NDK `28.2.13676358` was incomplete, and its required re-download failed with Windows `ERROR_DISK_FULL`.
- E-drive retry: build temp/Gradle/Android SDK paths were redirected to `E:\AI-Workspace-*` with ample free space, but SDK provisioning still left the NDK incomplete (`source.properties` missing) after the download attempt.
- Fresh debug APK build: passed after moving SDK tooling to E, reducing Gradle JVM memory, compiling against API 36, and upgrading `file_picker`/`flutter_secure_storage` compatibility.
- Physical install: passed on `CPH2371` (`U8OJ75WW7DT8YDSW`); package `com.example.ai_workspace_mobile`, version `1.0.0`, target SDK 36.
- Launch smoke check: passed; Android process remained alive after launch and no matching fatal Flutter/AndroidRuntime error appeared in the recent logcat sample.
- Product UI update: app branding changed to `Nexora AI` with animated login and chat empty/loading states.
- Product UI update: chat rooms and image studio are now reachable from the workspace drawer; image generation has an animated in-progress state.
- Product UI update: the disabled Video Studio tile is hidden from the mobile drawer.
- Product UI update: admin users now see an Admin console with analytics, read-only settings, users, audit export and transcript access.
- Final validation after UI update: `flutter analyze --no-fatal-infos` passed and a fresh debug APK was built successfully.
- Final device validation after UI update: APK installed successfully on `CPH2371` and the app process launched successfully.

## Not yet run

- Release APK and AAB build completion.
- iOS build (requires macOS/Xcode).
- Runtime chat message write/SSE stream and multipart upload on device/emulator.
- Document processing.
- Video generation and job completion.
- LiveKit join, camera, microphone and interruption behavior.
- Android physical/emulator and iOS simulator/device tests.

The remaining runtime items are live provider/device workflows (SSE response, uploads, document processing, video, LiveKit and iOS), not build or launch blockers.
