# AI Workspace Mobile API Mapping

Status: Phase 1 baseline, 2026-11-12.

This document separates source-verified contracts from live-verified behaviour. The deployed health endpoint was reachable and returned `{"status":"ok"}`. No authenticated test credentials were supplied, so authenticated workflows remain pending live verification.

## Base contract

- API prefix: `/api/v1`
- Authentication: JWT bearer token in `Authorization: Bearer <access_token>`.
- Common error body: `{"detail":"..."}`.
- Mobile must fail closed: never fabricate an assistant response when an API request fails.

## Source-verified endpoint map

| Feature | Method | Endpoint | Auth | Contract / mobile note |
|---|---:|---|---|---|
| Health | GET | `/health` | No | `{status: "ok"}`; live smoke checked. |
| Signup | POST | `/auth/signup` | No | `{identifier,password,full_name,dob}` -> token. |
| Login | POST | `/auth/login` | No | Identifier/email/mobile + password -> JWT token. |
| Current user | GET | `/auth/me` | Yes | User profile and admin flags. |
| Chat models | GET | `/chat/v2/models` | Yes | Model catalogue and capability restrictions. |
| Conversations | GET/POST | `/chat/v2/conversations` | Yes | List or create; support archived filtering and mode. |
| Conversation | GET/PATCH/DELETE | `/chat/v2/conversations/{id}` | Yes | Load, rename/configure, or delete. |
| Export conversation | GET | `/chat/v2/conversations/{id}/export` | Yes | Markdown/messages export. |
| Send message | POST | `/chat/v2/conversations/{id}/messages` | Yes | JSON content, model, attachment IDs, web search. |
| Stream message | POST | `/chat/v2/conversations/{id}/stream` | Yes | `text/event-stream`; parse `data:` events incrementally. |
| Regenerate | POST | `/chat/v2/conversations/{id}/regenerate` | Yes | Regenerate by message ID. |
| Attachments | POST/GET | `/chat/v2/attachments` | Yes | Multipart upload and attachment listing. |
| Attachment download | GET | `/chat/v2/attachments/{id}/download` | Yes | Binary response. |
| Rooms | GET/POST/PATCH/DELETE | `/rooms...` | Yes | Team chat, invitations, members, summaries. |
| Documents | POST | `/documents/extract`, `/summarize`, `/ask`, `/generate`, `/table` | Yes | Feature-gated; handle 404/503. |
| Images | POST | `/images/describe`, `/understand`, `/remove-background`, `/enhance`, `/variations` | Yes | Feature-gated multipart/image operations. |
| Office | GET/POST | `/office/formats`, `/office/generate` | Yes | Supported formats and generated files. |
| Research | POST | `/research/query`, `/research/search` | Yes | Feature-gated; sources and usage returned. |
| Video | POST/GET | `/video/generate`, `/image-to-video`, `/jobs`, `/jobs/{id}`, `/capabilities` | Yes | Async jobs; poll status and display provider/quota errors. |
| Voice | GET/POST | `/voice/status`, `/transcribe`, `/speak` | Yes | Feature-gated audio upload/bytes. |
| Calls | GET/POST | `/calls...` | Yes | Call lifecycle, messages, participants, LiveKit join. |
| Call events | WebSocket | `/calls/ws?token=...` | JWT query token | Incoming/accepted/ended events; close on lifecycle cleanup. |
| Admin | GET/PATCH/PUT | `/admin...` | Admin only | Users, quotas, providers, analytics, audit. Hide routes for non-admin users. |

## Live verification matrix

| Workflow | Evidence | Status |
|---|---|---|
| Backend health | `https://ai-workspace-gm23.onrender.com/api/v1/health` -> `{"status":"ok"}` | Verified |
| Login/signup | Admin and user credentials returned `200` with `access_token` and `token_type` | Verified |
| Profile/models/history | Admin and user: `/auth/me`, `/chat/v2/models`, `/chat/v2/conversations` returned `200`; conversation counts were 14 and 5 | Verified (read-only) |
| Attachments/rooms/media status | Admin/user attachment, room, video capability/jobs and voice status reads returned `200` | Verified (read-only) |
| Calls | Admin/user `/calls` returned `200`; LiveKit join and device media still require an active call test | Partially verified |
| Admin authorization | Admin admin endpoints returned `200`; regular user received `403` for users/analytics/provider models | Verified |
| Chat send/streaming and uploads | Requires a controlled write test and representative files | Pending |

## Implementation constraints

The client must use backend routes only; provider credentials never enter Flutter. `401` clears the session, `429` surfaces quota/rate-limit state, and `404/503` disables or explains unavailable feature-gated capabilities. Request/response details must be confirmed against authenticated traffic before marking a workflow complete.
