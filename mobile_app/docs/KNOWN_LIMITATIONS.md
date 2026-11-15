# Known Limitations

As of 2026-11-12:

- The repository's existing mobile client began as a prototype; generated Android/iOS projects now exist and the Chat v2 migration is underway.
- Live read-only API probes succeeded for both authorized roles. Runtime SSE streaming, multipart uploads, LiveKit join, and camera/microphone testing remain pending.
- The existing mobile UI still contains room-oriented prototype flows; chat v2 parity is being migrated incrementally.
- The Flutter SDK startup lock/cache issue was repaired. Android and iOS platform folders now exist, `flutter pub get` succeeds, and analysis/tests run. The first Android Gradle debug build did not finish within the verification window, so APK output still needs a dedicated Gradle/Android SDK run.
- No feature is marked complete solely because a source route exists; completion requires an authenticated mobile test result.
- The current workspace drive has very low free space; Flutter test compilation can fail with `OS Error: There is not enough space on the disk` until more temporary space is available.
