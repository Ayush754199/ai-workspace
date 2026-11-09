# AI Workspace v1.0 — feature status

Audit date: 2026-11-02. Rule set: existing behaviour preserved, nothing faked,
no hardcoded quota where the admin configures it, no paid provider enabled.

| # | Feature | Backend | Frontend | Tests |
|---|---|---|---|---|
| 1 | AI Chat — models, streaming, history, context, regenerate, retry, copy, export, attachments, model switch | `POST/GET /api/v1/chat/v2/*` | `/assistant` (`ChatWorkspace`) | `test_phases_chat_docs.py` |
| 2 | Image AI in chat — generate, edit, background removal, enhance, understanding, variations | `/chat/image`, `/api/v1/images/*` | `/images` + Chat "Image" mode on `/dashboard` | image tests |
| 3 | PDF & documents — chat, summarise, extract, generate, upload | `/api/v1/documents/*`, `/chat/v2/attachments` | `/documents` | doc tests |
| 4 | Office tools — Excel, Word, PowerPoint, CSV, PDF | `/api/v1/office/generate`, `/documents/generate` | `/office` | office test |
| 5 | Video Studio — prompt→video, image→video, pro editor | `/api/v1/video/generate`, `/video/image-to-video`, `/video/capabilities` | `/video-studio` (AI) + `/video-studio/editor` (pro) | video + editor tests |
| 6 | Voice AI — STT/TTS | `/api/v1/voice/*` | mic + read-aloud in chat | voice tests |
| 7 | Research — web search + sourced answers | `/api/v1/research/*` | `/research` | (network-dependent) |
| 8 | User management — signup, login, JWT, profile, roles | `/auth/*`, `/api/v1/admin/users` | login, register, profile | auth tests |
| 9 | Admin panel — providers, models, quotas, analytics, logs, toggles | `/api/v1/admin/*` | `/admin` | admin tests |
| 10 | AI Gateway — routing, fallback, reservations, retries, concurrency | `app/services/ai_gateway` | model picker shows real availability | gateway tests |
| 11 | Free API architecture — Gemini, Groq, OpenRouter, HF | `key_rotator`, gateway providers | — | gateway tests |
| 12 | Web + Android | — | responsive CSS + `capacitor.config.json` | UI tests |
| 13 | Security — auth, authorization, rate limit, validation, secrets, paid protection | `app/core/rate_limit.py`, `app/core/netguard.py`, `logger.py` redaction | inline errors, safe link rendering | security tests |

## Security hardening applied (2026-11-03)

* **CORS** is an allow-list (`CORS_ORIGINS`); wildcard only in local dev.
* **Legacy direct-provider endpoints** (`/ai/*`, `/chat/multimodal`, `/chat/stream`)
  return **410** by default (`LEGACY_DIRECT_AI_ENABLED=false`) — they bypassed quota
  and the free-provider policy.
* **SSRF guard** (`app/core/netguard.py`): research never fetches private,
  loopback, link-local or metadata addresses; redirects are re-validated.
* **Secret redaction** covers Gemini / Groq / OpenRouter / HF / LTX / FAL / Cloudflare
  key formats; logged errors are truncated and query strings stripped.
* **XSS**: Markdown answer rendering only allows `http(s)` links (`rel=noopener nofollow`).
* **Model verification is live**: admin "Test connection" runs a real provider probe.
* **Feature toggles are enforced** (503) on documents, office, images, research, voice, video.
* **Per-user concurrency** and **per-IP rate limits** on auth + AI routes.
* **Uploads** validated by extension, content-type, declared size and image decoding.
| 14 | Testing | — | — | 118 backend, 10 frontend |
| 15 | Deployment | `Dockerfile`s, `docker-compose.yml`, `scripts/backup.sh` | — | — |

## Known limitations (stated, not hidden)

* **Image-to-video / image editing** call a Hugging Face Space that must be
  configured (`HF_IMAGE_TO_VIDEO_SPACE`, `HF_IMAGE_EDIT_SPACE`). Unconfigured
  servers return a clear 503/disabled result.
* **OCR** needs a Gemini key; otherwise uploads report "no extractable text".
* **TTS** needs the `edge-tts` package or macOS `say`; otherwise 503.
* **Research** depends on DuckDuckGo HTML availability and the network.
* **Quota counters** are per-user and per-day, configured by admins; the daily
  default comes from `AI_DAILY_QUOTA`, not from code.
* **Rate limiting / concurrency** are per-process — run one worker (see
  `docs/DEPLOYMENT.md`).

## Where things live

```
backend/app/api/v1/endpoints/   auth, chat, chat_v2, documents, images, office,
                                research, voice, video, ai, admin, rooms, health
backend/app/services/           ai_gateway/, chat_service, documents, image_tools,
                                research, video_service, feature_flags
backend/tests/                  unit, API, quota, gateway failure, security
frontend/src/pages/             ChatWorkspace, Documents, ImageWorkspace, Office,
                                Research, Dashboard, AdminDashboard, Profile
frontend/src/video/             professional editor (project state, timeline, preview)
docs/                           DEPLOYMENT.md, ANDROID.md, this file
```
