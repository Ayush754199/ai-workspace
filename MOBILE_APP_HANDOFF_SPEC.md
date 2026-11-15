# AI Workspace — Flutter Mobile App Handoff Spec

> Source of truth: `backend/app/api/v1/endpoints/*` (75+ endpoints audited on 2026-11-11).
> Original project ko touch NAHI karna — ye doc sab kuch le jaata hai jo naye project me chahiye.

---

## 1. Backend Basics

- Base URL: `http://10.0.2.2:8000` (Android emulator → localhost) / `http://<PC-IP>:8000` (real device, same WiFi)
- Auth: **JWT Bearer token** — header `Authorization: Bearer <token>`
- Saare endpoints `/api/v1` prefix ke saath hain (health bhi: `/api/v1/health`)
- Error shapes: `{"detail": "<message>"}` (FastAPI standard)
- Status codes jo app ko handle karne hain: **400, 401, 403 (quota/policy), 404, 429 (rate-limit/quota-exhausted), 503 (provider/DB down)**

### Auth payloads
```jsonc
// POST /api/v1/auth/signup  -> {"access_token","token_type":"bearer"}
{"identifier": "user@example.com | 9876543210", "password": "...", "full_name": "...", "dob": "YYYY-MM-DD"}

// POST /api/v1/auth/login  -> Token
{"identifier": "...", "password": "..."}   // email field bhi chalega

// GET /api/v1/auth/me -> UserResponse
{"id","email","mobile_number","identifier","full_name","dob","role","is_active","is_admin","created_at"}
```
Note: `POST /api/v1/auth/register` legacy hai (email+mobile+full profile) — app me **signup** use karo.

---

## 2. Complete Endpoint Map

### 💬 Chat (PRIMARY FEATURE) — prefix `/api/v1/chat/v2`
| Method | Path | Body/Query | Response |
|---|---|---|---|
| GET | `/models` | — | `{models:[{id,label,provider,capabilities,free,selectable,reason}]}` |
| GET | `/conversations` | `?include_archived=` | `[Conversation]` (pinned-first, updated desc) |
| POST | `/conversations` | `{title?, system_prompt?, mode:"chat"\|"document"\|"research"}` | Conversation |
| GET | `/conversations/{id}` | — | `{...Conversation, messages:[Msg]}` |
| PATCH | `/conversations/{id}` | `{title?,model?,system_prompt?,is_pinned?,is_archived?}` | Conversation |
| DELETE | `/conversations/{id}` | — | `{deleted:id}` |
| GET | `/conversations/{id}/export` | — | `{title,markdown,messages}` |
| POST | `/conversations/{id}/messages` | `{content, model?, attachment_ids[], web_search?}` | `{user_message, message, provider, model}` |
| POST | `/conversations/{id}/regenerate` | `{message_id, web_search?}` | new assistant msg |
| POST | `/conversations/{id}/stream` | same as messages | **SSE stream** (`text/event-stream`) |
| POST | `/attachments` | multipart `file` (+fields) | attachment record `{id,filename,kind,content_type}` |
| GET | `/attachments` | query | list |
| GET | `/attachments/{id}/download` | — | binary |
| POST | `/documents/generate` | `{...}` | generated doc |

**Message shape:**
```jsonc
{"id","role":"user|assistant","content","provider","model","status":"complete",
 "attachments":[],"citations":[{"title","url","snippet","published_date"}...],
 "meta":{"request_id","routing":{"capability","complexity","reasons","preferred_provider"}},
 "created_at":"ISO"}
```
**Conversation shape:** `{id,title,model,provider,mode,system_prompt,is_pinned,is_archived,message_count,created_at,updated_at}`
Important: `web_search:true` par backend Tavily se citations laata hai — UI me citation chips render karo.

### 🏠 Rooms (team chat) — prefix `/api/v1/rooms`
| Method | Path | Notes |
|---|---|---|
| POST | `` | `{name, room_type:"group"}` → room |
| GET | `` | room list w/ `member_details[]`, `last_message_at`, `is_pinned` |
| PATCH | `/{id}` | `{name?,is_pinned?,is_archived?}` |
| DELETE | `/{id}` | delete |
| GET | `/{id}/messages` | `[{id,room_id,sender_id,sender_name,sender_role,content,mention_user_ids,created_at}]` |
| POST | `/{id}/messages` | `{content}` (max 20000) |
| POST | `/{id}/members` | `{identifier}` invite |
| GET | `/invitations` | pending invites |
| POST | `/invitations/{id}/accept` · `/decline` | — |
| DELETE | `/{id}/members/{user_id}` | kick |
| DELETE | `/{id}/membership` | leave |
| POST | `/{id}/invite-link` | shareable token link |
| POST | `/invites/{token}/accept` | join via link |
| POST | `/{id}/summary` | AI summary of room chat |
| GET | `/users/search?q=` | member picker |

### 🎨 Images — prefix `/api/v1/images` (feature-flagged → 404/503 possible)
- `POST /describe` (multipart file) — image captioning
- `POST /understand` (file + prompt) — visual Q&A
- `POST /remove-background` (file + params)
- `POST /enhance` (multipart file)
- `POST /variations` (file + params)

### 📄 Documents — prefix `/api/v1/documents`
- `POST /extract` (multipart file) — PDF/text extraction
- `POST /summarize` `{...}` · `POST /ask` `{...}` · `POST /generate` `{...}` · `POST /table` `{...}`

### 📊 Office — prefix `/api/v1/office`
- `POST /generate` — DOCX/XLSX/PPTX generation · `GET /formats` — supported list

### 🔍 Research — prefix `/api/v1/research`
- `POST /query` `{question(3-500), limit(1-10), topic?"general|news|finance"}` → `{answer/context, sources[], usage}`
- `POST /search` same body → `{query, sources[], usage}` (snippets only, cheaper)
- Errors: 429 (quota), 503 (search down), 404 (no results)

### 🎬 Video — prefix `/api/v1/video`
- `POST /generate` `{prompt,...}` · `POST /image-to-video` (file)
- `GET /jobs` · `GET /jobs/{id}` (poll for status) · `GET /capabilities`

### 🎙 Voice — prefix `/api/v1/voice` (feature-flagged)
- `GET /status` · `POST /transcribe` (audio file) · `POST /speak` `{...}` → audio bytes (header `X-TTS-Engine`)

### 📞 Calls (LiveKit) — prefix `/api/v1/calls`
- `POST `` {..}` → start call · `GET `` list · `GET /availability`
- `GET /{id}` · `GET /{id}/messages` · `POST /{id}/messages` · `POST /{id}/participants`
- `POST /{id}/accept|reject|cancel|end|join` — join returns LiveKit token+URL (use `livekit_client` package)

### 👑 Admin (is_admin only) — prefix `/api/v1/admin`
- `GET /users` · `GET /analytics` · `PATCH /users/{id}/status`
- `GET/PUT /users/{id}/quota` · `GET/PUT /users/{id}/quotas/{capability}`
- `GET/PUT /providers/models` · `GET /providers/openrouter/models`

### 🩺 Health — `GET /api/v1/health` (no auth)

---

## 3. Flutter Project Setup

```powershell
# Flutter SDK is at C:\src\flutter (not on PATH yet)
C:\src\flutter\bin\flutter doctor
C:\src\flutter\bin\flutter create ai_workspace_app --org com.aadiu
```

### pubspec.yaml dependencies
```yaml
dependencies:
  dio: ^5.7.0                    # HTTP client (interceptors ke saath)
  flutter_secure_storage: ^9.2.2 # JWT storage
  provider: ^6.1.2               # state management (ya riverpod)
  go_router: ^14.0.0             # navigation
  flutter_markdown: ^0.7.4       # chat rendering
  image_picker: ^1.1.2           # attachments
  file_picker: ^8.1.4            # doc uploads
  speech_to_text: ^7.0.0         # voice input
  flutter_tts: ^4.2.0            # fallback TTS
  livekit_client: ^2.x           # calls
  http_parser, mime              # multipart uploads
```

### Android cleartext (dev only) — `android/app/src/main/AndroidManifest.xml`
```xml
<application android:usesCleartextTraffic="true" ...>
```

### Ref code (already exists in `mobile_app/lib/services/api_service.dart` — copy patterns)
Login flow: `dio.post('/api/v1/auth/login')` → token → `GET /api/v1/auth/me` with Bearer → store in secure storage.

---

## 4. UI/UX Design (finalised)

### Design language
- **Dark-first** (chat apps ke liye): bg `#0E1116`, surface `#161B22`, primary accent `#6C5CE7` (violet), secondary `#00B894`
- Light theme toggle stored in secure storage
- Typography: Google Fonts **Inter** (UI) + **JetBrains Mono** (code blocks)
- Radius 16, subtle elevation, 8pt spacing grid
- Bottom nav: **Chats · Studio · Tools · Calls · Profile**

### Screen map
1. **Splash/Auth** — logo → login (identifier+password) → biometric unlock option (`local_auth`)
2. **Chats list** — conversations (pinned first, swipe: pin/archive/delete), search, FAB new chat, mode badge (chat/document/research)
3. **Chat screen** — bubbles (user right violet, AI left surface), markdown+code highlight, citation chips (tap → url_launcher), streaming via SSE (typewriter effect), attachment thumbnails above composer, model selector sheet from `/models`, mic button for STT
4. **Composer** — text field + attach (+) + model chip + send; long-press send = regenerate
5. **Studio tab** — segmented: Images (prompt → gallery grid, pick tool: generate/remove-bg/enhance/variations) · Video (prompt → job card with poll-to-complete progress) · Docs (upload → extract/summarize/ask/generate tabs, download result)
6. **Tools tab** — Research (question → sourced answer cards with links), Office (format picker from `/office/formats`), Voice (record → transcribe → speak)
7. **Calls tab** — call list + availability; ring UI on incoming (poll `/calls/availability`), LiveKit in-call screen (mute/camera/leave)
8. **Rooms** — team chat (secondary, from Chats tab drawer): room list → messages, invite member via user search, invitation accept/decline
9. **Profile** — user card, theme toggle, feature flags notice, logout, (admins: Admin link → users list + quota editor)
10. **Global UX** — offline banner, 503 = "provider unavailable, try again" toast, 429 = quota exhausted screen with reset time, pull-to-refresh everywhere

### State architecture
```
lib/
  core/ (api_client.dart with auth interceptor + 401 auto-logout, theme.dart, constants.dart)
  models/ (conversation.dart, message.dart, room.dart, user.dart — freezed ya manual)
  services/ (auth_service, chat_service, media_service, research_service, call_service)
  providers/ (auth_provider, chat_provider, ...)  // ChangeNotifier
  screens/ + widgets/
```
Key rule: **401 → token clear → login redirect; 429/503 → user-friendly banner; kabhi bhi mock reply mat dikhana (backend fail-closed hai)**.

### Streaming (chat)
`POST /chat/v2/conversations/{id}/stream` SSE deta hai — Dio me `ResponseType.stream` se read karo, `data:` chunks parse karke bubble me append karo. Fallback: non-stream POST endpoint.

---

## 5. Gotchas
1. Signup vs register: **signup** use karo.
2. `/api/v1/chat` (legacy) gated hai — sirf **`/chat/v2`** use karo.
3. Feature-flagged routes (documents/images/office/research/voice) 404 ya 503 de sakte hain agar disabled — UI me gracefully disable karo.
4. Mobile numbers normalized hoti hain (7–15 digits) — validation client-side bhi mirror karo.
5. Attachment IDs send_message ko `attachment_ids[]` me jaate hain — pehle `/attachments` upload karo, phir ids pass karo.
6. Real device testing: PC ka LAN IP + firewall me port 8000 allow.
