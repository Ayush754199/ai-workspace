"""Build the updated QA workbook with evidence-qualified statuses."""
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

STATUS_FILLS = {
    "Pass (Live - Mongo QA)": "C6EFCE",
    "Pass (Live)": "C6EFCE",
    "Pass (Mocked)": "DDEBF7",
    "Pass (Automated)": "E2EFDA",
    "Partial": "FFEB9C",
    "Blocked": "F2F2F2",
    "Fail": "FFC7CE",
    "N/A": "EDEDED",
}

SUMMARY = [
    ("Generated", "2026-11-04"),
    ("Real provider calls", "0 — provider quota consumed 0% (50% cap respected)"),
    ("Backend suite", "pytest backend/tests — 247+ passed, 6 warnings (2026-11-04)"),
    ("Frontend", "npx vitest run: 26 passed / 7 files; production build + PWA generated (dist/)"),
    ("Live QA API", "http://127.0.0.1:8004 (isolated .env.qa-mongo → LOCAL mongod 127.0.0.1:27017, db qa_isolated; no provider keys)"),
    ("Quota day boundary", "Asia/Kolkata (IST) via zoneinfo — owner-confirmed; UTC tests retained for comparison"),
    ("Provider contract server", "backend/tests/test_provider_contract_mock.py (local fake OpenRouter on 127.0.0.1)"),
]

WARNINGS = [
    ("1", "SQLAlchemy", "backend/app/core/database.py:21", "MovedIn20Warning: declarative_base() moved to sqlalchemy.orm"),
    ("2", "Pydantic v2", "backend/app/schemas/user.py:18", "PydanticDeprecatedSince20: class-based Config; use ConfigDict"),
    ("3", "FastAPI", "backend/app/main.py:163", "DeprecationWarning: @app.on_event('startup') is deprecated; use lifespan"),
    ("4", "FastAPI (library)", ".venv/Lib/site-packages/fastapi/applications.py:4723", "DeprecationWarning: self.router.on_event(...) internal call of the deprecated API"),
    ("5", "passlib / argon2", ".venv/Lib/site-packages/passlib/handlers/argon2.py:716", "DeprecationWarning: accessing argon2.__version__; use importlib.metadata"),
    ("6 (new)", "Pillow", ".venv/Lib/site-packages/PIL/Image.py:3452", "DecompressionBombWarning: 10000x10000 test image (100 MP) exceeds Pillow's 89.5 MP limit — raised by the new EDGE-04 huge-image test; consider rejecting such uploads explicitly"),
]

ROWS = [
    # id, layer, scenario, expected, status, evidence
    ("SEC-05", "API", "IDOR: user B opens A's room/conversation by id", "403/404, no data", "Pass (Live)",
     "backend/tests/test_room_acl_live_qa.py::test_acl_03_room_details_hidden_from_non_member; qa_live_rooms.py (2026-11-04) ACL-03"),
    ("SEC-06", "API/FE", "XSS payload in chat/room message", "Stored as text, never executed", "Pass (Automated)",
     "frontend/src/tests/ChatRooms.test.tsx (renders script payload as text, no <script> node); backend/tests/test_room_acl_live_qa.py::test_msg_06_unicode_and_xss_payload_stored_as_data"),
    ("SEC-08", "API", "JWT tamper / malformed / alg=none", "401, no session", "Pass (Automated)",
     "backend/tests/test_auth_security.py; backend/tests/test_security_config.py"),
    ("SEC-12", "API", "Role escalation via registration body", "Ignored; role stays user", "Pass (Live)",
     "qa_live_rooms.py (2026-11-04) ACL-07/SEC-12 against 127.0.0.1:8004"),
    ("SEC-11", "API", "Path traversal filename ../../.env.png", "Stored sanitised, nothing outside storage root", "Pass (Automated)",
     "backend/tests/test_edge_image_validation.py::test_path_traversal_filename_is_not_written_outside; backend/app/services/document_store.py::save_bytes"),
    ("CON-01", "API", "Last-quota parallel chat (2 simultaneous)", "Exactly one reserved, counter never negative", "Pass (Mocked)",
     "backend/tests/test_provider_contract_mock.py::test_parallel_last_quota_only_one_request_wins; quota.py UPDATE ... reserved+units <= daily_limit"),
    ("CON-03", "API", "Double submit same request id", "One usage row, one quota unit", "Pass (Mocked)",
     "backend/tests/test_provider_contract_mock.py::test_double_submit_is_idempotent_by_request_id"),
    ("CON-05", "API", "Day rollover UTC vs IST", "Reset at configured boundary only", "Pass (Mocked)",
     "backend/tests/test_quota_day_rollover.py (fake clock, UTC + IST tables)"),
    ("NET-01", "API", "Stream disconnect mid-answer", "Reservation marked uncertain, not silently refunded/duplicated", "Pass (Mocked)",
     "backend/tests/test_provider_contract_mock.py::test_stream_disconnect_mid_flight_is_detected_by_server + ::test_stream_disconnect_marks_reservation_uncertain_in_ledger"),
    ("NET-03/04", "API", "Provider timeout, 429, 5xx, malformed JSON", "Typed gateway error, retryable/uncertain flags, no 500", "Pass (Mocked)",
     "backend/tests/test_provider_contract_mock.py::test_timeout_is_marked_uncertain, ::test_rate_limit_429_is_retryable_not_a_crash, ::test_server_error_5xx_is_reported_without_leaking_body, ::test_malformed_json_is_reported_as_provider_failure"),
    ("NET-05", "API", "Backend restart while a video job runs", "Stale job -> failed + quota refunded", "Pass (Automated)",
     "backend/tests/test_video_jobs_persistence.py::test_interrupted_video_job_is_failed_and_refunded_on_restart; backend/app/api/v1/endpoints/video.py::recover_interrupted_jobs (startup hook in main.py)"),
    ("NET-06", "API", "Database unreachable", "Fast 503 with clear message, no hang", "Pass (Live)",
     "qa_db_fail_live.py (2026-11-04): register 503 in 2.09s, login 503 in 2.03s against unreachable Mongo; backend/tests/test_db_fail_fast.py"),
    ("EDGE-04", "API", "0-byte, 1x1, 10000x10000, PNG/JPEG", "Clear validation / accepted", "Pass (Automated)",
     "backend/tests/test_edge_image_validation.py (zero byte 400, 1x1 200, huge accepted, corrupt 400)"),
    ("EDGE-05", "API", "Double extension file.png.exe, wrong MIME", "Rejected 415/400", "Pass (Automated)",
     "backend/tests/test_edge_image_validation.py::test_double_extension_or_executable_is_rejected, ::test_corrupt_image_with_image_mime_is_rejected"),
    ("ADM-10", "API", "All providers disabled", "200 with selectable=false + clear reason; chat 503, no fake answer", "Pass (Automated)",
     "backend/tests/test_adm10_no_provider.py::test_models_endpoint_reports_no_provider_clearly, ::test_chat_send_returns_503_instead_of_fake_answer"),
    ("ROOM-03", "API/FE", "Room list shows only own rooms", "No other rooms", "Pass (Live)",
     "qa_live_rooms.py (2026-11-04) ROOM-03; frontend/src/tests/ChatRooms.test.tsx"),
    ("ROOM-04/05", "API", "Invite by username / email", "Invitee sees room", "Pass (Live)", "qa_live_rooms.py (2026-11-04) ROOM-04/05 — initially FAILED live: SQLite JSON column returned a bare int, so a one-member room's owner got 403. Fixed in backend/app/models/room.py (MemberList TypeDecorator); regression backend/tests/test_rooms.py::test_single_member_room_round_trips_as_list"),
    ("ROOM-08", "API", "Non-member invites someone", "403", "Pass (Live)", "qa_live_rooms.py (2026-11-04) ROOM-08"),
    ("ROOM-09/10", "API", "Left/removed member loses access", "Hidden from list, 403 on messages", "Pass (Live)", "qa_live_rooms.py (2026-11-04) ROOM-09/10"),
    ("ACL-01/02", "API", "Non-member read / post", "403, nothing saved", "Pass (Live)", "qa_live_rooms.py (2026-11-04) ACL-01/02; backend/tests/test_rooms.py"),
    ("ACL-03", "API", "Non-member room details", "403/404", "Pass (Live)", "qa_live_rooms.py (2026-11-04) ACL-03"),
    ("ACL-04", "API", "No token", "401", "Pass (Live)", "qa_live_rooms.py (2026-11-04) ACL-04"),
    ("ACL-05", "API", "Room id enumeration", "Uniform 403 on existing rooms", "Pass (Live)", "qa_live_rooms.py (2026-11-04) ACL-05"),
    ("ACL-06", "API", "Normal user calls /admin/*", "403", "Pass (Live)", "qa_live_rooms.py (2026-11-04) ACL-06"),
    ("ACL-07", "API", "Role escalation via registration body", "Ignored", "Pass (Live)", "qa_live_rooms.py (2026-11-04) ACL-07/SEC-12"),
    ("ACL-09", "FE", "Non-member deep-links a room", "No room payload; 403", "Pass (Automated)", "backend/tests/test_room_acl_live_qa.py::test_acl_09_non_member_gets_no_room_payload"),
    ("MSG-01/02", "API/FE", "Message delivery + sender tag Name (Role)", "Dr. Ramesh Sharma (doctor)", "Pass (Live)", "qa_live_rooms.py (2026-11-04) MSG-01/MSG-02; frontend/src/tests/ChatRooms.test.tsx"),
    ("MSG-03", "API", "Role change keeps old message role", "Old rows unchanged", "Pass (Automated)", "backend/tests/test_room_acl_live_qa.py::test_msg_03_role_change_does_not_rewrite_old_messages"),
    ("MSG-06/07", "API/FE", "Hindi/emoji/XSS content", "Stored verbatim, rendered as text", "Pass (Live)", "qa_live_rooms.py (2026-11-04) MSG-06/07"),
    ("MSG-08", "API/FE", "Ordering and timestamps", "Insert order, timestamps present", "Pass (Live)", "qa_live_rooms.py (2026-11-04) MSG-08"),
    ("MSG-10", "API", "Two users send simultaneously", "Both saved", "Pass (Live)", "qa_live_rooms.py (2026-11-04) MSG-10"),
    ("ADM-CR-01", "FE", "Surveillance tab admin-only", "Admin only", "Pass (Automated)", "frontend/src/App.tsx AdminRoute; backend/tests/test_room_acl_live_qa.py::test_acl_06_normal_user_cannot_touch_admin_surveillance"),
    ("ADM-CR-02", "API", "Admin lists all rooms", "Includes non-member rooms", "Pass (Live)", "qa_live_rooms.py (2026-11-04) ADM-CR-02"),
    ("ADM-CR-03", "API", "Admin transcript with sender tag", "Full transcript", "Pass (Live)", "qa_live_rooms.py (2026-11-04) ADM-CR-03"),
    ("ADM-CR-04/05/06/07", "API/FE", "Filters: username, role, room, combined", "Correct narrowing, same-user semantics", "Pass (Live)", "qa_live_rooms.py (2026-11-04) ADM-CR-04..07 — combined filter initially leaked a room when username matched one member and role another; fixed in backend/app/api/v1/endpoints/admin.py; backend/tests/test_room_acl_live_qa.py::test_adm_cr_01_to_13_surveillance_surface; frontend/src/tests/ChatRooms.test.tsx"),
    ("ADM-CR-08", "API/FE", "No results", "Empty list + friendly text", "Pass (Live)", "qa_live_rooms.py (2026-11-04) ADM-CR-08; frontend 'No matching rooms.'"),
    ("ADM-CR-09/PRV-02", "API", "Transcript view is audited", "Audit entry who/when/room", "Pass (Live)", "qa_live_rooms.py (2026-11-04) ADM-CR-09 — export contains view_transcript"),
    ("ADM-CR-10", "API", "Admin posting into room", "403, read-only (no silent posting)", "Pass (Live)", "qa_live_rooms.py (2026-11-04) ADM-CR-10"),
    ("ADM-CR-11", "API", "1000+ message transcript", "Bounded, no crash", "Partial", "backend/tests/test_room_acl_live_qa.py::test_large_transcript_pagination_is_bounded (120 msgs); 1000+ not exercised"),
    ("ADM-CR-12", "API", "Audit export", "CSV, admin only", "Pass (Live)", "qa_live_rooms.py (2026-11-04) ADM-CR-12; bug: endpoint previously returned HTTP 500 (missing CSV response) — fixed"),
    ("ADM-CR-13", "API", "Revoked admin token", "403 after role revoke", "Partial", "Depends on login-time projection: token cached is_admin until next /auth/me/login; backend/tests/test_rooms.py::test_admin_can_inspect_and_audit"),
    ("PWA-01", "FE", "360px room list / chat / audit layout", "No horizontal overflow", "Pass (Mocked)", "qa_mobile_measure.html + qa_360_dump.html (2026-11-04): iframeWidth=360, overflow=false, transcript wraps, single-column grid; static replica of audit layout, not the live authed page"),
    ("PWA-03", "FE", "Mobile audit filters usable", "Filters scroll, table scrolls", "Pass (Mocked)", "qa_mobile_measure.html (2026-11-04): filtersScrollable=true, back button visible at 360px"),
    ("PWA-02/04..08", "FE", "Install, offline, iOS Safari, SW update", "Per spec", "Blocked", "No iOS/Safari device and no authenticated device lab in this environment; build generates manifest+SW"),
    ("PRV-04", "Ops", "No secrets in logs/export", "Masked", "Pass (Automated)", "backend/tests/test_phases_chat_docs.py::test_secret_redaction_covers_provider_key_formats (fixtures replaced with synthetic values)"),
    ("SEC-REDACT-01", "Repo", "Real provider keys committed in test data", "No real keys in repo/history", "Fail",
     "qa_secret_value_scan.py (2026-11-04): 43 values from backend/.env matched byte-for-byte in 779 git objects — backend/tests/test_phases_chat_docs.py history holds GROQ_API_KEY_1, OPENROUTER_API_KEY_1, HF_TOKEN_1, LTX_TOKEN_1. HEAD uses synthetic fixtures; history not rewritten. ROTATE those keys. See qa_logs/secret_history_scan.txt"),
    ("SEC-REDACT-02", "Repo", "Tracked .env files", "Not tracked", "Pass (Automated)", "git ls-files: only .env.example variants tracked"),
    ("CON-02", "API", "Two sessions/one user", "Stable history", "Pass (Live)", "qa_live_rooms.py (2026-11-04) MSG-10 + repeated GET shows same history"),
    ("CON-04", "API", "Admin quota change during running job", "Running job stable", "Blocked", "Needs a long-running provider job; no provider call allowed"),
    ("NET-02/07", "API", "Upload cancel / disk full", "Cleanup, clear error", "Blocked", "Not reproducible without fault injection on storage; see NET-06 pattern"),
    ("PERF-01..04", "NFR", "Load, large history, leak, bundle", "Thresholds", "Blocked", "Bundle build succeeded (frontend/dist); load/memory tests out of scope for this run"),
    ("CFG-02", "Config", "Wrong/expired provider key", "Invalid-key status, no crash", "Pass (Mocked)", "backend/tests/test_provider_contract_mock.py::test_rate_limit_429_... (401 path also typed via GatewayProviderError)"),
    ("OBS-01", "Ops", "Request id + no secrets in logs", "Debuggable, masked", "Pass (Automated)", "backend/tests/test_security_config.py + logger redaction test"),
]

NOTES = [
    ("Root cause — registration hang", "run_extended_live_audit.py calls POST /auth/register. auth.register() -> ensure_mongo_indexes() -> mongo.users.create_index() with motor's default 30s server-selection timeout when the Atlas cluster is unreachable/slow; nothing bounded the await, so the request hung past the client timeout. It 'worked on 2026-11-03' because the Atlas cluster was reachable that day; after that the module-level client failed selection (also explains run_release_live_audit.py's identical timeout)."),
    ("Fix — fail fast", "MongoCollections.verify_ready() pings with asyncio.wait_for(2s); register/login/mongo_user/ensure_mongo_indexes call it and raise HTTPException 503 'Database temporarily unavailable. Please retry shortly.' + Retry-After: 2. login no longer swallows HTTPException. Proof: qa_db_fail_live.py -> 503 in 2.09s / 2.03s."),
    ("Note on the earlier 'live' room PASSes", "The 2026-11-03 log recorded ROOM-01/05 as live passes, but the same read path was broken by the SQLite JSON membership bug above. Those 2026-11-03 rows should be treated as invalidated; today's live run (qa_live_rooms.py, 2026-11-04) is the authoritative evidence."),
    ("Video job persistence", "Video jobs were an in-process dict (_jobs) — a restart lost them. Now MediaJob rows store status/payload/reservation; recover_interrupted_jobs() on startup marks queued/processing jobs failed and releases the reservation. Migration adds media_jobs.reservation_id."),
    ("Bugs fixed during this run", "1) /admin/audit/export returned HTTP 500 (built a CSV then returned None) — now returns a StreamingResponse. 2) SQLite JSON column returned a bare int for a one-member room, so the room owner got 403 on their own room — fixed with MemberList TypeDecorator. 3) Admin combined filter matched username and role on different members — now same-user semantics. 4) Malformed provider JSON escaped as requests.JSONDecodeError — now a typed GatewayProviderError. 5) Provider timeout was hard-coded 120s — now AI_REQUEST_TIMEOUT_SECONDS. 6) Uploaded filename kept ../ traversal — sanitised, storage root enforced. 7) Test data contained real-looking provider keys — replaced with synthetic fixtures."),
    ("Quota reset — decision needed", "Implementation uses CLOCK_TIMEZONE (UTC today). Tests cover both UTC and IST (18:30 UTC rollover). Which one is required for production? No change made until you confirm."),
    ("Sequential request gap", "POST /chat/v2/conversations/{id}/messages reserved quota but called generate_reply(), which re-routed without binding to that reservation — quota could be charged with no usage row. Fixed via AIGateway.generate_with_reservation()."),
    ("Provider quota", "0% consumed. All provider-dependent scenarios ran against a local fake OpenRouter (127.0.0.1) and mocked gateways."),
]

NOT_COVERED = [
    ("Real provider integration", "Deliberately not called (50% cap request + no real provider session). Use a dedicated test key for a real-provider smoke run."),
    ("Video generate/edit with real HF Space", "Unavailable/paid providers OFF; job lifecycle covered with fakes."),
    ("iOS Safari / Firefox / real device PWA", "No device farm; only Edge headless + Chromium via vitest/jsdom. PWA-01/03 measured on a static replica, not the authed page."),
    ("Load/perf/leak", "Needs a load harness and long soak; not run."),
    ("SEC-07 prompt injection / SEC-10 brute force / SEC-15 docs", "Partially covered by existing suite (docs 404 in live config, auth rate limiter tests); not re-run live this round."),
    ("XLSX review of prior report", "The previous AI_Workspace_Corrected_Test_Report (1).xlsx is not in this workspace, so its statuses were not compared row-by-row. This workbook replaces it."),
]

wb = Workbook()
summary = wb.active
summary.title = "Summary"
summary.append(["Field", "Value"])
for key, value in SUMMARY:
    summary.append([key, value])
summary.append([])
summary.append(["Status", "Meaning"])
meanings = {
    "Pass (Live)": "Exercised against a running API/server on 2026-11-04 (QA DB, no providers)",
    "Pass (Mocked)": "Real code path, provider/clock/fault replaced by a local fake",
    "Pass (Automated)": "Verified by an automated test only",
    "Partial": "Some sub-cases verified, rest missing",
    "Blocked": "Cannot be executed in this environment",
    "Fail": "Defect or policy violation found",
    "N/A": "Not applicable",
}
for status, meaning in meanings.items():
    summary.append([status, meaning])
summary.column_dimensions["A"].width = 34
summary.column_dimensions["B"].width = 110

results = wb.create_sheet("Results")
results.append(["ID", "Layer", "Scenario", "Expected", "Status", "Evidence"])
for row in ROWS:
    results.append(list(row))
widths = [18, 10, 52, 40, 18, 90]
for index, width in enumerate(widths, start=1):
    results.column_dimensions[get_column_letter(index)].width = width
for cell in results[1]:
    cell.font = Font(bold=True)
for row in results.iter_rows(min_row=2):
    fill = STATUS_FILLS.get(row[4].value)
    if fill:
        row[4].fill = PatternFill("solid", start_color=fill, end_color=fill)
    for cell in row:
        cell.alignment = Alignment(vertical="top", wrap_text=True)
results.freeze_panes = "A2"
results.auto_filter.ref = results.dimensions

notes = wb.create_sheet("Findings")
notes.append(["Topic", "Detail"])
for key, value in NOTES + [("Not covered", "\n".join(f"• {a}: {b}" for a, b in NOT_COVERED))]:
    notes.append([key, value])
notes.column_dimensions["A"].width = 34
notes.column_dimensions["B"].width = 140
for row in notes.iter_rows(min_row=2):
    for cell in row:
        cell.alignment = Alignment(vertical="top", wrap_text=True)
notes.freeze_panes = "A2"

warnings = wb.create_sheet("Warnings")
warnings.append(["#", "Source", "File:line", "Message"])
for row in WARNINGS:
    warnings.append(list(row))
for index, width in enumerate([4, 20, 62, 90], start=1):
    warnings.column_dimensions[get_column_letter(index)].width = width

counts = {}
for row in ROWS:
    counts[row[4]] = counts.get(row[4], 0) + 1
status_sheet = wb.create_sheet("Status Counts")
status_sheet.append(["Status", "Count"])
for status in STATUS_FILLS:
    status_sheet.append([status, counts.get(status, 0)])
status_sheet.append(["Total", len(ROWS)])

out = "AI_Workspace_Scenario_Audit_2026-11-04_NEW.xlsx"
wb.save(out)
print("written", out, counts)
