# Android Build

The Android project must be generated inside `mobile_app/` with Flutter and built only from the feature branch.

```powershell
C:\src\flutter\bin\flutter.bat create --org com.aiworkspace --platforms=android,ios --project-name ai_workspace_mobile .
C:\src\flutter\bin\flutter.bat pub get
C:\src\flutter\bin\flutter.bat analyze
C:\src\flutter\bin\flutter.bat build apk --release --dart-define=AI_WORKSPACE_API_URL=https://ai-workspace-gm23.onrender.com
C:\src\flutter\bin\flutter.bat build appbundle --release --dart-define=AI_WORKSPACE_API_URL=https://ai-workspace-gm23.onrender.com
```

For local Android emulator testing use `http://10.0.2.2:8000`; production uses HTTPS. Camera, microphone, notifications and media permissions must be declared in the generated Android manifest and tested on a physical device.
