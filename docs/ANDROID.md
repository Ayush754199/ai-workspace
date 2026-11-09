# Android app (Capacitor)

The web app in `frontend/` builds to static files and Capacitor wraps them in a
native Android WebView. The same API base URL logic is used on device: it points
at the host of the loaded app origin, so set `VITE_API_BASE_URL` when building.

## One-time setup

```bash
cd frontend
npm install @capacitor/core @capacitor/cli @capacitor/android @capacitor/splash-screen @capacitor/status-bar
npx cap add android
```

Requires Android Studio + SDK and JDK 17.

## Build for the backend URL you run

```bash
# On device only reachable over the LAN/internet, so pass the public backend URL:
VITE_API_BASE_URL=https://api.example.com npm run android:sync
npm run android:open      # or: npm run android:run
```

`capacitor.config.json` sets `webDir: "dist"` (the Vite output) and uses the
`https` scheme for the local origin, which is required for the WebView to allow
camera/microphone prompts for the voice features.

## Permissions

Voice AI needs a microphone. Add to `android/app/src/main/AndroidManifest.xml`:

```xml
<uses-permission android:name="android.permission.RECORD_AUDIO" />
<uses-permission android:name="android.permission.INTERNET" />
```

A plain WebView `MediaRecorder` grants `RECORD_AUDIO` at runtime through
`onPermissionRequest` of `WebChromeClient` (Capacitor already grants audio and
video capture when `captureInput` is enabled in `capacitor.config.json`).

## What works on device

Everything is server-rendered behaviour, so the whole product works if the
backend is reachable: chat, streaming (SSE over fetch), documents, office file
downloads, image AI, research, admin panel. The video *editor* uses browser
`<video>`, canvas thumbnails and `localStorage`, all of which the Android WebView
supports; local blob URLs are re-imported after a reload by design.

## Release build

```bash
cd frontend && npm run android:sync
cd android && ./gradlew bundleRelease
```

Signing is configured in `android/app/build.gradle` with your keystore; never
commit keystore files or passwords.
