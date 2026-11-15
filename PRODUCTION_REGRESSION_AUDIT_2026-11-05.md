# AI Workspace v1.0 — Production Regression Audit (2026-11-05)

## Scope and safety
- Inspected existing frontend/backend routes, provider registry/adapters, chat and room models, document helpers, video services, admin routes, and existing tests before changing code.
- No database was dropped/reset, no existing `.env` or credential file was read for values or modified, no live/paid model was deliberately called, and no test accounts were added to a non-QA database.
- Test execution and isolated QA environment are being verified separately. No mocked result is classified as a live provider result.

## Findings before fixes
| Issue | Root cause / evidence | Affected files | Risk | Proposed/implemented action |
|---|---|---|---|---|
| Web-search failure could silently downgrade to ordinary chat | `chat_v2.send_message` caught all research exceptions and continued with the original prompt, empty citations. | `backend/app/api/v1/endpoints/chat_v2.py` | Medium; user can mistake an unsourced answer for searched/current information. | Changed requested-search failures and empty source/context responses to explicit HTTP 503; answers are constrained to supplied source context. |
| Concurrent room-message test asserted request-start order | Server persists each message and room history returns increasing database IDs; simultaneous HTTP requests have no guaranteed start-to-commit order. | `backend/tests/test_missing_scenarios.py`, `backend/app/api/v1/endpoints/rooms.py` | Low; changing application semantics to force a request-start order needs a product requirement and distributed sequencing mechanism. | Replaced brittle fixed `tab-0` ordering expectation with both-message persistence, stable monotonic ID ordering, and refresh consistency assertions. No behavior was weakened. |
| CSV table parsing used simple comma split | Quoted values containing commas (e.g. `"Doe, Jane"`) were split into multiple columns. | `backend/app/services/documents.py` | Medium; corrupted generated CSV/XLSX contents. | Added standard-library CSV parsing for comma-separated input while preserving pipe-table parsing. |
| Core deterministically generated document route exists separately from AI-authoring Office route | `/documents/generate` renders user-provided content without a model; `/office/generate` first asks an AI and will return 503 if no provider. | `backend/app/api/v1/endpoints/documents.py`, `office.py` | Feature expectation risk, not necessarily a bug; preserve existing APIs. | Added artifact-level regression for PDF/XLSX/CSV generation from supplied data; Office AI authoring still correctly reports unavailable when AI is absent. |
| Image instruction editing depends on configured HF edit Space | `/chat/image` requires configured `HF_IMAGE_EDIT_SPACE`, sends image+prompt, fails clearly otherwise. | `backend/app/api/v1/endpoints/chat.py`, `ImageWorkspace.tsx` | High for advertised feature if no eligible Space exists. | Do not fake edit; live test blocked absent configuration. |
| Video routes depend on configured Hugging Face Spaces and tokens; actual artifact URL/format not proven | `video.py`, `video_service.py`, `media_service.py`; capability readiness is configuration, not artifact verification. | Video endpoint/services | High for production acceptance. | Keep truthful blocked status absent verified free-provider credentials/Space; test jobs/refunds with existing suite. |
| OpenRouter free routing is policy-filtered, but local provider proof is not live | Registry uses `openrouter/free`; only free allowlisted routes; credentials required; no eligible model in isolated runtime previously. | Gateway registry/provider/config | High for production acceptance. | Contract tests are mocks only; a live call is blocked absent usable configured provider credentials and should not be inferred from mock tests. |

## Audit rerun results (2026-11-07)
- Targeted backend auth/rooms/artifact/search/DB-fail-fast regression run: **43 passed**.
- Targeted backend provider/quota/media/editor run: **53 passed** (`test_gateway_foundation.py`, `test_provider_contract_mock.py`, `test_adm10_no_provider.py`, `test_quota_day_rollover.py`, `test_video_jobs_persistence.py`, `test_image_workflow.py`, `test_advanced_editor.py`).
- Frontend: **27 passed / 7 files** after stabilizing jsdom storage and URL origin. Production Vite build: **passed**; PWA assets generated.
- Full backend suite was attempted but exceeded the command time limit. Its captured partial run (existing `qa_logs/final_backend_suite3.log`) reported **247 passed, 1 failed** in `test_old_day_reserved_rows_become_uncertain`; a focused rerun of `tests/test_quota_day_rollover.py` passed **10/10** in this session using SQLite with Mongo disabled. The earlier failure is not reproduced; full-suite status remains incomplete rather than confirmed defective.
- MongoDB Atlas: a uniquely named disposable `aiworkspace_audit_*` collection was used for insert/read/update, then read via a fresh MongoClient to verify persistence, and the probe document was deleted. Existing collections/documents were not modified. The two audit collections created by this run were explicitly removed after verifying they contained only the run’s test document. A later Atlas metadata/list-collections attempt was blocked by DNS timeout. Configured database is `enterprise_ai_db`. Credentials were pasted in the request and should be rotated.
- Browser: login rendered; invalid credentials returned the expected 401 message. Isolated SQLite registration/login reached dashboard. A fresh isolated backend on port 8001 with a disposable SQLite file completed two-user registration/login, conversation create/list/read after page reload, user-B denial (404), and owner cleanup. The browser reports the expected authorization 404 as a failed network request; workflow assertions passed. This is local SQL evidence, not Atlas-backed identity/chat persistence.
- Browser found an implicit `/favicon.ico` 404. Linked the existing SVG icon; retest showed **zero console errors and zero failed requests**.

Previous reports below summarize earlier findings; pending statuses there are historical and superseded by the rerun summary above.

| Test ID | Module | Scenario | Expected result | Actual result | Status | Root cause | Fix |
|---|---|---|---|---|---|---|---|
| WEB-FAIL-01 | AI chat/research | Search network fails while web_search=true | Explicit unavailable response; no unsourced model call | Pending test run | Pending | Silent catch-and-continue | 503 failure handling + regression test |
| CON-ROOM-02 | Rooms | Two concurrent messages and refresh | Both persisted, stable DB ordering | Pending test run | Pending | Test demanded scheduler-dependent order | Reliable ordering assertion |
| DOC-CSV-01 | Document parser | Quoted comma and UTF-8 data | Values stay in proper columns | Pending test run | Pending | Naive `split(',')` | CSV standard parser + artifact test |
| DOC-FILES-01 | PDF/XLSX/CSV | Verify emitted artifacts contents | Real parsable output with proper data | Pending test run | Pending | Verify output bytes and parsed content | Artifact-level regression test |

## Live-provider cases
- OpenRouter free chat/fallback: **BLOCKED unless safe configured credentials and at least one eligible verified free route are present.** A fake OpenRouter server is a contract simulation, not live proof.
- Web research: local live public internet search may be tested once if network is available; any AI-backed answer also requires an eligible configured free route.
- Image editing, text-to-video, image-to-video: **BLOCKED unless real free-capable endpoints and credentials/configuration are enabled.** Never substitute image transforms or a job ID for the required artifact.
- Paid models/providers: not used.
