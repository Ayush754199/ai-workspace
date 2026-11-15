# iOS Build

The iOS project must be generated inside `mobile_app/` with Flutter and built from the feature branch.

```powershell
C:\src\flutter\bin\flutter.bat pub get
C:\src\flutter\bin\flutter.bat analyze
C:\src\flutter\bin\flutter.bat build ios --release --dart-define=AI_WORKSPACE_API_URL=https://ai-workspace-gm23.onrender.com
```

iOS release signing and simulator/device builds require macOS/Xcode. Add camera, microphone, photo-library and document-picker usage descriptions before testing calls or attachments. No iOS release build is claimed from this Windows workspace.
