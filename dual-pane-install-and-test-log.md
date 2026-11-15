# Dual-Pane Chat / Artifact Routing — install and verification log
**Date:** 2026-11-07
## Node.js update request
- Existing runtime before attempt: Node.js `v18.18.0`, npm `9.8.1`.
- Attempted `winget install --id OpenJS.NodeJS.LTS --exact` (without accepting agreements or choosing an alternate package source).
- `winget list --id OpenJS.NodeJS.LTS --exact` did not find the installed package after the first attempt. A second install was started in the background, then stopped at the user's request.
- Installer/winget processes were stopped if present. Recheck afterward still reported `v18.18.0` / npm `9.8.1`; no Node upgrade is claimed.
- No project files, `.env` values, API keys, or database credentials were edited by the install operation.
- Warning/blocker: browser automation's Node runner did not return a usable browser report when invoked through this shell; no supported browser binary was available to its fallback. No screenshot or browser console/network capture was produced.

## Application bring-up attempts
- Attempted to launch FastAPI with the current environment and frontend Vite dev server; actual listener/HTTP health responses were not confirmed. Later check found no listener on 8000/5173; an existing `node` process (PID 25968) remained but was not verified as Vite.
- No valid test login was supplied. We did not create an account using the configured external Mongo database.
- No chat/artifact prompt was sent; therefore no AI provider call or Tavily credit was spent.
- No tests (automated or functional) were run, per request to pause Node installation and test only with current installs; browser dependencies/runtime prevented browser-driven testing.

## Functional scenario report
| Scenario | Expected | Actual result | Status |
|---|---|---|---|
| Start backend/frontend | API and UI listen locally | Launch attempted; listeners/health not confirmed | BLOCKED |
| Render existing UI in browser | Login/workspace visible and clean console/network | Browser runner yielded no usable report; no screenshot | BLOCKED |
| Generate PDF-style artifact | Live assistant emits `file_pdf`, opened in right preview | Not requested to provider / no authenticated browser session | NOT RUN |
| Generate Excel/CSV artifact | CSV grid displayed in right preview | Not run | NOT RUN |
| Generate code artifact | Code displayed in right preview | Not run | NOT RUN |
| Keep artifact out of chat stream | Chat only has concise intro; artifact in preview | Not run | NOT RUN |
| Regular non-artifact chat | Short conversational reply | Not run | NOT RUN |
| Download / Copy / Print / CSV-to-XLS | Controls work in browser | Not run | NOT RUN |
| Preview open/close/switch/responsive | Layout responds at desktop/mobile sizes | Not run | NOT RUN |
| Browser console / backend logs | No new errors | Browser capture unavailable; no scenario-specific logs | NOT RUN |

## Rerun update — 2026-11-07
- Existing Node/npm were used; no install was needed.
- Browser run against Vite login: page rendered; invalid login response was 401 with expected inline message. The initial implicit favicon request was 404; fixed by linking the already-present SVG icon. Follow-up browser report had zero console errors and zero failed requests.
- Isolated local SQLite registration/login worked and the dashboard rendered. No authorized production test account was supplied; no Atlas identity or records were altered.
- Atlas ping and a reversible test CRUD flow passed in a unique disposable collection: insert/read/update, fresh-client persistence read, then delete of the probe document. No existing collections/documents were modified. Metadata listing later hit DNS timeout. Credentials were exposed and need rotation.
- Frontend automated tests: 27/27 pass after test-environment storage fix. Production frontend build passes. Targeted backend tests: 43/43 pass. Full backend suite exceeded the command time limit; an earlier captured run showed 247 pass / 1 fail, but a focused quota rollover run passed 10/10 with Mongo disabled, so full-suite status is incomplete rather than a confirmed failure.
- A two-user browser ACL/persistence flow passed against a fresh backend instance on port 8001 and a disposable SQLite DB: register/login, conversation create/list/read after reload, User B denied, owner cleanup. This demonstrates app-level ACL and local SQL persistence only, not Atlas-backed identity/chat storage.

## Remaining blocked work
1. Rotate all credentials exposed in the pasted request and any previously committed keys noted in the 2026-11-04 audit.
2. Supply/authorize a test login after credential rotation before testing an existing account.
3. Re-run Mongo persistence, collection/index, restart and CRUD checks against a designated non-production Atlas database, or authorize a separate safe test namespace; never create/delete test records in production without approval.
4. Re-run the full backend suite with a longer process budget to confirm the earlier partial-run failure report; the focused quota rollover file passed 10/10 in this audit.
5. Live AI/Tavily/image/video requests remain blocked pending rotated usable credentials, provider-safe test approval, and confirmed provider readiness. Paid calls were not made.
