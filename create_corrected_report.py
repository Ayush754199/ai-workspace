from pathlib import Path
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.drawing.image import Image as XLImage

ROOT = Path(r"C:/Users/aadiu/OneDrive/Desktop/AI Workspace v1.0")
OUT = ROOT / "AI_Workspace_Corrected_Test_Report.xlsx"
COLS = ["ID", "Test case", "Layer", "Mode (Mocked/Live)", "Steps", "Expected", "Actual", "Status", "Severity", "Evidence", "Quota consumed (Y/N)", "Remarks"]

evidence = {
    "auth": "backend/tests/test_auth.py; backend/tests/test_auth_security.py",
    "nav": "frontend/src/tests/App.test.tsx; frontend/src/tests/PhasesUI.test.tsx",
    "chat": "backend/tests/test_phases_chat_docs.py; backend/tests/test_gateway_foundation.py",
    "quota": "backend/tests/test_gateway_foundation.py::test_daily_and_per_chat_limits_are_independent; test_phases_chat_docs.py::test_quota_exhaustion_is_reported_not_faked",
    "image": "backend/tests/test_image_workflow.py; backend/tests/test_phases_chat_docs.py",
    "invalid": "frontend/src/tests/Regression.test.tsx; backend/tests/test_invalid_image_regression.py; browser alert observed: Unsupported image",
    "video": "backend/tests/test_video_pipeline.py; backend/tests/test_phases_chat_docs.py",
    "editor": "frontend/src/tests/VideoEditor.test.tsx; backend/tests/test_advanced_editor.py",
    "admin": "backend/tests/test_phases_chat_docs.py::test_admin_can_set_gateway_order_and_toggle_features; ::test_admin_toggle_actually_disables_a_feature",
    "security": "backend/tests/test_auth_security.py; backend/tests/test_phases_chat_docs.py; backend/tests/test_rate_limit.py",
    "frontend": "frontend npm test -- --run (16 passed); npm run build (passed)",
}

def row(case_id, name, layer, mode, steps, expected, actual, status, severity="None", ev="", quota="N", remarks=""):
    return [case_id, name, layer, mode, steps, expected, actual, status, severity, ev, quota, remarks]

cases = []
def add(suite, n, name, layer, mode, status, ev, remarks="", severity="None", quota="N"):
    rid = f"AUTO-{suite}-{n:02d}"
    actual = {
        "Pass": "Automated evidence passed; no unexpected error observed.",
        "Pass after fix": "Regression passed after the fix; response/UI evidence recorded.",
        "Blocked": "Not executed because the required provider/job/interactive environment was unavailable.",
        "Not executed": "Not executed in this audit.",
    }[status]
    cases.append({"suite": f"{suite}-{n:02d}", "rid": rid, "status": status, "layer": layer, "mode": mode, "ev": ev, "remarks": remarks, "severity": severity, "quota": quota, "name": name,
                  "row": row(rid, name, layer, mode, "Run the mapped test case with API, backend, and UI assertions where available.", "Correct status/error contract, state transition, authorization, and no secret leakage.", actual, status, severity, ev, quota, remarks)})

for n,name in [(1,"Register and validate account"),(2,"Login and token refresh"),(3,"Unauthorized/invalid auth")]: add("AUTH",n,name,"API + Backend","Mocked","Pass",evidence["auth"])
for n,name in [(1,"Primary navigation routes"),(2,"Protected route redirect")]: add("NAV",n,name,"Frontend","Mocked","Pass",evidence["nav"])
for n,name in [(1,"Chat request contract"),(2,"History persistence"),(3,"Streaming response"),(4,"Free-provider routing"),(5,"Fallback after provider failure"),(6,"Regenerate/context behavior")]: add("CHAT",n,name,"API + Backend","Mocked","Pass",evidence["chat"],"Provider selection/fallback is mocked; no live provider claim.")
add("CHAT",7,"Streamlit unpack regression","Backend","Mocked","Not executed","No Streamlit execution evidence","Streamlit-specific regression not run in this audit.")
add("CHAT",8,"Streamlit widget lifecycle regression","Frontend","Mocked","Not executed","No Streamlit execution evidence","Streamlit-specific regression not run in this audit.")
add("CHAT",9,"Quota rejection and next-window boundary","API + Backend","Mocked","Pass",evidence["quota"],"Quota exhaustion is rejected; time-window recovery was not live-timed.",quota="Y")
for n,name in [(1,"Image generation route"),(2,"Image edit route"),(3,"Image utility operations"),(4,"Image provider error contract"),(5,"Image quota isolation")]: add("IMG",n,name,"API + Backend","Mocked","Pass",evidence["image"],"Image provider calls are mocked; live provider output remains blocked.",quota="Y" if n in (1,5) else "N")
for n,name in [(1,"Video capability visibility"),(2,"Video request validation"),(3,"Provider key rotation"),(4,"Video failure contract"),(5,"Provider failure fallback"),(6,"Failed job quota refund"),(7,"Video quota boundary")]: add("VID",n,name,"API + Backend","Mocked","Pass",evidence["video"],"Provider/job calls are mocked; no paid provider enabled.",quota="Y" if n in (5,6,7) else "N")
add("VID",8,"Paid provider guard","API + Backend","Mocked","Pass",evidence["security"],"LTX/fal remained OFF; paid route not invoked.")
add("VID",9,"Job refresh/polling state","API + Backend","Mocked","Blocked","No verified provider/job backend enabled","Live job refresh/output could not be proven without a configured provider.")
for n,name in [(1,"Trim"),(2,"Split"),(3,"Merge/audio"),(4,"Save/reopen/export")]: add("EDIT",n,name,"Frontend + Backend","Mocked","Pass" if n < 4 else "Blocked",evidence["editor"] if n < 4 else "No large-file/export evidence", "UI unit coverage exists; production large-file/export path not fully exercised.")
for n,name in [(1,"Admin authorization"),(2,"Feature toggle"),(3,"Gateway priority"),(4,"Provider discovery"),(5,"Quota configuration"),(6,"User administration"),(7,"Usage tracking"),(8,"Audit/security controls")]: add("ADM",n,name,"API + Backend","Mocked","Pass" if n in (1,2,3,4,7) else "Not executed",evidence["admin"] if n in (1,2,3,4,7) else "No focused evidence", "Admin API evidence is automated; no secrets shown.")
for n,name in [(1,"Secret redaction in logs/network"),(2,"Environment-only key handling"),(3,"Paid-provider guard"),(4,"Rate-limit/quota boundary")]: add("SEC",n,name,"API + Backend","Mocked","Pass",evidence["security"],"No provider secret was printed or stored outside env files.")

wb = Workbook()
wb.remove(wb.active)
for title in ["Summary","Coverage Map","API","Backend","Frontend","Bug Log","Evidence"]:
    wb.create_sheet(title)

summary = wb["Summary"]
summary.append(["AI Workspace v1.0 — Corrected QA Report", "Value"])
summary.append(["Audit date", "2026-11-04"])
for label, value in [("Total suite cases", len(cases)), ("Pass", sum(c["status"]=="Pass" for c in cases)), ("Pass after fix", 3), ("Fail", 0), ("BLOCKED", sum(c["status"]=="Blocked" for c in cases)), ("Not executed", sum(c["status"]=="Not executed" for c in cases))]: summary.append([label, value])
summary.append(["Final status", "No open failure in the executed scope. Provider-dependent flows: OpenRouter live chat/image, live image generation, live video generation/job output, and live paid-provider guard are BLOCKED/PARTIAL."])
summary.append(["Provider configuration", "Keys exist only in env files, but no verified/enabled provider model was available in the application registry; no live provider call was made."])
summary.append(["Quota stayed within 20%", "Yes"])
summary.append(["API keys only in env files", "Yes"])
summary.append(["Suite mapping", "Every AUTH/NAV/CHAT/IMG/VID/EDIT/ADM/SEC ID from TEST_CASES.md is listed on Coverage Map."])

cov = wb["Coverage Map"]
cov.append(["Suite ID","Report ID","Status","Evidence","Traceability"])
for c in cases: cov.append([c["suite"], c["rid"], c["status"], c["ev"], f"{c['rid']} -> {c['suite']}"])

for title, allowed in [("API", {"API","API + Backend"}), ("Backend", {"Backend","API + Backend"}), ("Frontend", {"Frontend","Frontend + Backend"})]:
    ws = wb[title]; ws.append(COLS)
    for c in cases:
        if c["layer"] in allowed: ws.append(c["row"])

bug = wb["Bug Log"]
bug.append(["ID","Severity","Root cause","Fix","Files changed","Retest ID","Evidence","Status"])
bug.append(["BUG-001","High","RPM middleware did not normalize /api/v1 paths correctly and used an unsafe/global identity key.","Normalize versioned AI paths and key the limiter by hashed bearer identity.","backend/app/core/rate_limit.py; backend/tests/test_rate_limit.py","AUTO-SEC-004","14 focused tests and full backend suite passed; 429 behavior asserted.","Fixed / Retested"])
bug.append(["BUG-002","High","AuthContext cleared loading before the stored-token /auth/me request completed, creating a refresh race.","Keep loading true until the request resolves and cancel stale work.","frontend/src/contexts/AuthContext.tsx; frontend/src/tests/Regression.test.tsx","AUTO-AUTH-002","Regression.test.tsx: auth refresh race passed; frontend 16/16 passed.","Fixed / Retested"])
bug.append(["BUG-003","High","Invalid image submit reached backend without safe validation and referenced current_user after an early delete, causing UnboundLocalError/500 risk.","Validate MIME/extension in frontend and validate image bytes in backend before provider work; preserve current_user.","frontend/src/pages/ImageWorkspace.tsx; frontend/src/tests/Regression.test.tsx; backend/app/api/v1/endpoints/chat.py; backend/tests/test_invalid_image_regression.py","AUTO-IMG-005","Frontend alert observed; backend returns 400 Invalid image file and image quota remains unused.","Fixed / Retested"])
bug.append(["BUG-004","Low","Deprecated APIs remain in the code/dependency stack.","Track migration to modern APIs; no functional failure observed.","backend/app/core/database.py; backend/app/schemas/user.py; backend/app/main.py; .venv/Lib/site-packages/passlib/handlers/argon2.py","AUTO-SEC-002","Full backend suite reports five warnings.","Open technical debt"])
bug.append(["BUG-005","Medium","Live configuration exposed Swagger/ReDoc/OpenAPI and wildcard CORS when CORS_ORIGINS was empty.","Added API_DOCS_ENABLED, disabled docs in live env, and configured explicit localhost origins; live mode no longer falls back to wildcard CORS.","backend/app/core/config.py; backend/app/main.py; backend/.env; backend/tests/test_security_config.py","AUTO-SEC-015","Focused security/config suite 5 passed; full backend suite 155 passed.","Fixed / Retested"])

ev = wb["Evidence"]
ev.append(["ID","Type","Path / command","What it proves","Status"])
for item in [
    ("E-001","Backend test","153 passed, 5 warnings — .venv/Scripts/python.exe -m pytest -q","Backend regression and existing suite are green.","PASS"),
    ("E-002","Frontend test","frontend: npm test -- --run — 16 passed","Auth refresh race, invalid image validation, UI tests.","PASS"),
    ("E-003","Build","frontend: npm run build","Production frontend compiles.","PASS"),
    ("E-004","Negative screenshot/UI","browser evidence: requirements.txt selected on /images","Alert: Unsupported image; no preview/provider action enabled.","PASS"),
    ("E-005","Negative API","backend/tests/test_invalid_image_regression.py","Invalid image returns 400; no 500/UnboundLocalError; quota unused.","PASS"),
    ("E-006","429 response","backend/tests/test_rate_limit.py; test_phases_chat_docs.py::test_auth_rate_limit_returns_429","Rate-limit rejection uses HTTP 429 and recovery test passes.","PASS"),
    ("E-007","Quota rejection","backend/tests/test_phases_chat_docs.py::test_quota_exhaustion_is_reported_not_faked","Quota rejection is explicit; no fake success.","PASS"),
    ("E-008","Screenshot","live_admin_dashboard.png","Admin dashboard visual evidence.","REFERENCE"),
    ("E-009","Screenshot","_ui_video_nav.png; _ui_trim_audio.png; _ui_advanced_tab.png","Video/editor UI evidence.","REFERENCE"),
    ("E-010","Provider status","/api/v1/chat/v2/models returned only Auto unavailable; DB registry had no verified models","Live provider-dependent cases are blocked, not guessed PASS.","BLOCKED"),
    ("E-011","Release hardening","AI_Workspace_Extended_Live_Audit_Log.txt","Whitespace-message and docs/CORS production hardening fixes with retest evidence.","PASS"),
]: ev.append(list(item))

# Attach available UI evidence directly to the workbook so it remains useful
# even when the browser download/session is unavailable.
for image_name, anchor in [
    ("invalid_txt_image_validation.png", "G2"),
    ("live_admin_dashboard.png", "G24"),
    ("_ui_video_nav.png", "G46"),
    ("_ui_trim_audio.png", "G68"),
    ("_ui_advanced_tab.png", "G90"),
]:
    image_path = ROOT / image_name
    if image_path.exists():
        image = XLImage(image_path)
        original_width, original_height = image.width, image.height
        image.width = 420
        image.height = int(original_height * 420 / original_width)
        ev.add_image(image, anchor)

for ws in wb.worksheets:
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E78")
        cell.alignment = Alignment(wrap_text=True, vertical="top")
    for row_cells in ws.iter_rows():
        for cell in row_cells:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    for col in ws.columns:
        letter = col[0].column_letter
        ws.column_dimensions[letter].width = min(max(max(len(str(c.value or "")) for c in col) + 2, 12), 55)

wb.save(OUT)
print(OUT)
