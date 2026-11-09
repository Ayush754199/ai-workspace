# Full production test log — 2026-11-09

Environment tested:

- Frontend: `https://ai-workspace-frontend-907h.onrender.com`
- Backend: `https://ai-workspace-gm23.onrender.com`
- Mobile viewport: 320 × 800
- Initial testing mode: regression only; fixes were applied afterward in separate commits.

## Passed checks

| Area | Result | Evidence |
|---|---|---|
| User login/profile | PASS | Supplied user login and `/auth/me` succeeded |
| Room creation | PASS | Created QA room successfully |
| Room message send/read | PASS | Normal message returned HTTP 200 |
| Blank message validation | PASS | HTTP 422 with `Message cannot be empty` |
| Oversized message validation | PASS | 20,001-character message returned HTTP 422 |
| XSS message storage | PASS | Payload was returned as text; no execution observed |
| Image permission block | PASS | User without grant returned HTTP 403 |
| Video permission block | PASS | `/video/capabilities` returned `user_permitted: false` |
| Mobile dashboard | PASS | No horizontal overflow at 320px; scroll width 305px |
| Mobile images/documents/office/video | PASS | No horizontal overflow at 320px |
| Frontend deep links | PASS | `/dashboard`, `/assistant`, `/documents`, `/images`, `/office`, `/research`, `/chat`, `/video-studio`, `/video-studio/editor`, `/profile`, `/admin` returned HTTP 200 |

## Issues logged for later fixing

### FT-001 — Direct authentication routes return 404

- Severity: P1
- Reproduction: Open `/login` or `/register` directly in a new browser tab.
- Actual: HTTP 404 from the frontend static host.
- Expected: Login/register SPA screen should load directly.
- Workaround: Open `/` first; it redirects to `/login` and works.

### FT-002 — Fresh QA registration timed out

- Severity: P1 / intermittent infrastructure
- Reproduction: Full live audit attempted a new `/auth/register` request with a 15-second timeout.
- Actual: HTTPS read timeout during registration; the automated audit could not continue with fresh accounts.
- Expected: Registration should complete within normal request time or return a controlled error.
- Note: Existing supplied-user login succeeded, so this was isolated to fresh registration latency during this run.

### FT-003 — Supplied admin credential is not an administrator

- Severity: P1 / test-blocking configuration
- Reproduction: Login with `7541996529 / AKrishna@123`, then call `/auth/me` and `/api/v1/admin/video/users`.
- Actual: `/auth/me` reports `role=user`, `is_admin=false`; admin endpoint returns HTTP 403.
- Impact: Admin-side image/video grant and revoke flow could not be executed end-to-end.

### FT-004 — Provider-backed generation remains unavailable in the deployed environment

- Severity: P2 / environment configuration
- Actual: Video capability reports provider readiness false. Image generation cannot be positively tested after grant without a valid admin and configured provider credentials.
- Impact: Actual image/video output quality and provider success path remain unverified in production.

## Resolution status after the test

- FT-001 resolved in commit `77c0374`: direct `/login` and `/register` now return HTTP 200 after deployment.
- Mobile and workspace deep-link fixes from the earlier release remain green.
- FT-002 remains open for backend/Atlas latency investigation.
- FT-003 remains open until the production SQL row for `7541996529` is explicitly promoted with `is_admin=true`.
- FT-004 remains open until video/image provider spaces and credentials are configured; both prompt-to-video and image-to-video currently return the expected permission block for the supplied normal user (HTTP 403).

## Follow-up admin/media verification

- FT-003 resolved: live `/auth/me` for `7541996529` now reports `role=admin` and `is_admin=true`.
- Admin API successfully granted `image_generation` and `video_generation` to the supplied user.
- User capability check now reports `user_permitted: true` for video.
- Image generation reached the provider layer and returned HTTP 503 because `HF_TOKEN`/image endpoint is not configured.
- Prompt-to-video and image-to-video reached the provider layer and returned HTTP 502 with quota release because the configured video provider spaces are unavailable.
- Therefore access control is verified end-to-end; successful media output still requires provider credentials/spaces.

## Not marked as issues

- Room normal send, validation, XSS text handling, permission denial, mobile overflow, and listed workspace deep links passed in this run.
- No code or deployment change was made as part of this testing-only pass.
