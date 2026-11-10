# Intelligent AI Orchestration Engine — Audit and Implementation Plan

Date: 2026-11-10

## Audit summary

The current application already has a reusable FastAPI gateway, authenticated chat-v2 conversations, SSE streaming, provider health/fallback primitives, server-side quota reservations, Tavily-backed research, secure attachment storage, PDF/DOCX/XLSX/PPTX extraction and generation, and an admin provider/model surface.

The main gaps are architectural rather than a missing chat screen:

- `chat_service.generate_reply()` currently delegates route choice to the gateway's provider order; it does not score intent, complexity, follow-up context, files, or capability requirements.
- The registry only proves explicitly verified database models. Muse has no adapter, credentials, model records, or budget ledger.
- Cloudflare is currently used by the legacy image endpoint, but its daily account allowance is not configured or tracked in neuron units. Research processing is not split from image processing at the Cloudflare account level.
- Research uses real Tavily search/extraction and returns sources, but model analysis is still ordinary gateway chat and the streaming/chat paths contain separate research logic.
- Uploaded office files are extracted locally, but page/sheet/slide provenance is flattened into text and scanned-PDF OCR is only an optional Gemini fallback.
- Image generation/editing is implemented in a legacy endpoint with several fallback providers; it is not yet constrained to the requested Muse Image/Cloudflare policy.
- Usage logs exist, but they do not yet record an orchestration decision, intent, capability plan, budget bucket, or verified provider cost.
- Video endpoints and UI exist from earlier work. They remain out of scope for this upgrade and must stay disabled for v1.0.

## Controlled implementation phases

1. **Provider registry and capability verification** — add explicit provider adapters and capability records. A provider/model is routable only after credentials, endpoint, capability, and a successful admin verification probe are present.
2. **Context-aware orchestration** — add an intent assessment object containing task type, complexity, freshness requirement, file/image requirements, follow-up references, and quality/cost constraints. Route simple chat to Groq, complex reasoning to verified Muse Spark, multimodal/search-grounded work to verified Gemini/appropriate workflows, and never send image work to a text-only route.
3. **Research workflow** — keep real search/retrieval separate from analysis, attach source metadata and citations to chat messages, and reserve a dedicated Cloudflare research bucket when Cloudflare analysis is used.
4. **Image workflow** — route only supported generation/editing operations to verified image models, add the separate Cloudflare image bucket, and remove unverified fallback claims.
5. **Context-preserving switching** — build provider-neutral context from persisted conversation turns and attachment references before every route choice. Make the request id the idempotency boundary for streaming and retries.
6. **Documents** — preserve page, slide, and worksheet provenance; add scanned-PDF OCR as an explicit capability with a clear unavailable state; keep local extraction/generation free of AI calls.
7. **Observability and admin reporting** — record intent, selected route, fallback chain, provider/model, capability bucket, units, cost, status, and failure reason. Add admin views and controls for verification, budgets, and quota health.
8. **Verification** — run mocked backend/provider-contract tests, frontend tests, security/access-control tests, then optional live probes only after the account credentials and Muse API contract are supplied.

## External blockers and safe defaults

The repository does not contain a Muse API contract, endpoint, model identifiers, or credentials. The implementation must not invent these values. Muse Spark and Muse Image therefore remain disabled until the operator supplies the endpoint/model contract and completes a successful admin verification probe.

The Cloudflare model catalog can verify model availability, but the model catalog does not report the account's effective daily neuron allowance or current billing plan. The application therefore requires `CLOUDFLARE_DAILY_NEURON_LIMIT` to be configured from the Cloudflare account dashboard/API. A zero or missing value fails closed. The two buckets are fixed at 50% each with no transfer of unused capacity.

## Acceptance criteria

- Auto routing is based on a structured intent assessment and verified capability registry, not a frontend model name or a keyword-only switch.
- A follow-up can change providers without losing persisted context or duplicating a message/request.
- Research has real source URLs and inline/source metadata citations; unavailable search never produces fabricated citations.
- Image generation/editing never falls back to a text-only model and fails clearly when no supported image provider is verified.
- Muse Spark and Muse Image stop at independent monthly USD caps of 7 dollars.
- Cloudflare research/image usage is tracked in neurons in independent 50% daily buckets, reset by configured account policy, with no cross-bucket transfer.
- All provider credentials stay backend-only, and video remains disabled in v1.0.
