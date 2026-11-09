# Production full test report — 2026-11-09

## Verdict

Not ready for mobile-app rollout yet. Core chat and office documents pass, and the persistent Postgres-backed admin permission flow is working. Image generation, prompt-to-video, and image-to-video still return provider errors because the configured Gemini/LTX credentials are invalid or unavailable. Browser-to-backend CORS and the mobile login page are now reachable.

## Verified pass

- Admin login works.
- Admin can grant `image_generation` and `video_generation` to the user; both permission updates returned HTTP 200.
- After permission grant, the user capability endpoint reports both features permitted.
- Permissions persist in the Render Postgres database; the admin bootstrap boolean migration was corrected for PostgreSQL.
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

- Image generation: the configured Cloudflare Workers AI token now passes a direct image-generation request; the app's previous `cfat_` token exclusion was incorrect and has been removed. Gemini key 1 is invalid, while Gemini key 2 is quota-exhausted.
- Prompt-to-video: HTTP 502, `Video generation failed; quota was released`.
- Image-to-video: HTTP 502, `Image-to-video failed; quota was released`.
- Cloudflare token verification endpoint returns 401, but the account's Workers AI image endpoint accepts the same configured token and returns image data; the application now follows the working endpoint result.
- Video editor had horizontal overflow on tablet/iPad/laptop widths. A CSS containment fix was committed in `frontend/src/styles.css`; the mobile login smoke check at 390px reported no document overflow.
- Browser login API fetch now succeeds from the production frontend origin with HTTP 200.

## Changes made

- Production AI gateway default changed from development mock to live provider mode.
- Production frontend origin added to the backend CORS default.
- Video editor timeline overflow constrained to its own scroll area at widths up to 1080px.
- User media permissions were re-granted from the admin account for the verification run.
- Native Gemini image-generation fallback was added, using the current Gemini image-output API; the live key test returned HTTP 401, so a valid Gemini key must be set before image generation can pass.
- A free Render Postgres database was linked to the backend service. It expires on 2026-12-09 unless upgraded or replaced.
- A FAL queue fallback was added for image, text-to-video, and image-to-video. Validation showed both configured FAL accounts are locked because their balances are exhausted, so this fallback will become usable after credit is added or a funded key is supplied.
- Validation showed both Hugging Face tokens authenticate, but the configured `Wan-AI/Wan2.1` Space is in `RUNTIME_ERROR`; this is an upstream Space failure, not an application permission failure. LTX API keys authenticate and accept jobs, but the job fails with `Insufficient funds`.
- Cloudflare's available model search did not expose a text-to-video or image-to-video generation model; it can support the image path, not the video path in this configuration.

