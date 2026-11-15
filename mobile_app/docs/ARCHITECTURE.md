# Mobile Architecture

The Flutter client is isolated under `mobile_app/` and communicates only with the existing FastAPI API. It must not contain provider credentials or duplicate backend business rules.

## Layers

- `lib/core/`: API base URL, error mapping, secure-session rules, theme and shared constants.
- `lib/services/`: Dio transport and feature clients for auth, chat, files, media, research and calls.
- `lib/models/`: typed API DTOs; preserve server IDs and timestamps.
- `lib/providers/`: Riverpod application state; legacy prototype widgets may be migrated incrementally.
- `lib/screens/`: mobile workflows and platform-aware layouts.
- `lib/widgets/`: reusable message, attachment, loading, error and empty-state components.
- `docs/`: verified API/feature contracts and release evidence.

## Session and error policy

JWT is stored in platform secure storage. A restored token is validated with `/auth/me`; invalid sessions are removed. `401` logs out, `403` shows permission state, `429` shows quota/rate-limit state, and `404/503` marks feature-gated capabilities unavailable. The client never creates fake assistant messages.

## Delivery order

1. Auth/session and system theme.
2. Chat v2 conversations, message rendering and SSE.
3. Attachments and document/image workflows.
4. Video jobs and files.
5. Calls/LiveKit and device permissions.
6. Admin, quotas, settings and release testing.
