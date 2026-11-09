# Production full test report — 2026-11-09

## Verdict

Not ready for mobile-app rollout yet. Core chat and office documents pass, but image generation, prompt-to-video, and image-to-video return provider errors. The latest backend CORS fix is deployed through Render but was still `In progress` at the time of this report, so the browser login gate must be rerun after it becomes live.

## Verified pass

- Admin login works.
- Admin can grant `image_generation` and `video_generation` to the user; both permission updates returned HTTP 200.
- After permission grant, the user capability endpoint reports both features permitted.
- Chat uses the live OpenRouter provider after the mock-provider production fix (`provider=openrouter`, `model=openrouter/free`).
- Chat-room creation, normal messages, blank-message validation, long-message validation, and script-text safety were exercised.
- Excel generation: HTTP 200, valid XLSX content type.
- PDF generation: HTTP 200, valid PDF content type.
- Word generation: HTTP 200, valid DOCX content type.
- Excel read/extract: HTTP 200.
- PDF read/extract: HTTP 200, one page and expected text.
- Word read/extract: HTTP 200, expected text.
- Public/protected route responsive smoke checks found no root horizontal overflow except the video editor timeline area.

## Failed / blocked

- Image generation: HTTP 502, `Configured image generation provider failed`.
- Prompt-to-video: HTTP 502, `Video generation failed; quota was released`.
- Image-to-video: HTTP 502, `Image-to-video failed; quota was released`.
- The supplied Cloudflare credential verifies as an active token, but its screenshot identifies it as an R2 S3-compatible token; it is not sufficient for the Workers AI image endpoint.
- Video editor had horizontal overflow on tablet/iPad/laptop widths. A CSS containment fix was committed in `frontend/src/styles.css`, but live verification waits for the current frontend deploy.
- Browser login was blocked by missing production CORS origin. A source default was added in `backend/app/core/config.py`; Render deploy was still in progress when this report was written.

## Changes made

- Production AI gateway default changed from development mock to live provider mode.
- Production frontend origin added to the backend CORS default.
- Video editor timeline overflow constrained to its own scroll area at widths up to 1080px.
- User media permissions were re-granted from the admin account for the verification run.

