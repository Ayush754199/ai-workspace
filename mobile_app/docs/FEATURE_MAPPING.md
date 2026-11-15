# AI Workspace Mobile Feature Mapping

Status: Phase 1 baseline, 2026-11-12. “Planned” means the mobile implementation is not yet complete; it is not a claim of parity.

| Existing web capability | Flutter destination | Backend surface | Status / verification gap |
|---|---|---|---|
| Login, signup, session | Auth flow + secure token store | `/auth/signup`, `/auth/login`, `/auth/me` | Prototype exists; authenticated live test pending. |
| AI chat and history | Chats list, chat screen, composer | `/chat/v2/conversations*` | Conversation UI, real message send and SSE chunk handling implemented; model selection and regeneration remain. |
| Markdown, code, citations | Message renderer | Chat message response | Planned; response shape needs live capture. |
| Attachments and document chat | Composer attachments + document actions | `/chat/v2/attachments`, `/documents/*` | Real multipart attachment upload wired; document actions and feature flags remain. |
| Image generation/editing tools | Studio > Images | `/images/*` and existing provider routes | Planned; deployed capability status pending. |
| Video generation | Studio > Video job cards | `/video/*` | Capabilities, generation request and job history screens wired; runtime generation/provider result still pending. |
| Professional video editor | Editor screen and local project state | Existing web/editor APIs if exposed | Gap: no mobile contract confirmed yet; must audit before implementation. |
| Voice features | Tools > Voice | `/voice/*` | Planned; provider/streaming behavior pending. |
| Voice/video calls | Calls list and lifecycle actions | `/calls/*`, `/calls/ws`, LiveKit join response | Calls list and accept/reject/end lifecycle wired; LiveKit media room and two-party device test remain. |
| Rooms/team chat | Rooms drawer and message screen | `/rooms/*` | Prototype service methods exist; UI parity pending. |
| My Files | Files screen | Attachment/download and generated-file routes | Gap: complete file listing/download contract needs audit. |
| Profile/settings/theme | Profile and settings | `/auth/me` plus persisted local settings | System theme and persistence must be added. |
| Usage/credits/quotas | Usage screen and quota states | Admin/quota and response metadata | User-facing contract needs authenticated capture. |
| Admin controls | Admin-only screens | `/admin/*` | Planned; must test role-based hiding and 403 behavior. |

## Parity rule

No row is complete until the corresponding live workflow has been exercised with authorized credentials, its actual request/response and error behavior recorded, and the Flutter path tested against the same backend data as the web app.
