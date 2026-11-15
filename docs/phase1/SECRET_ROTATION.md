# Credential rotation list (Phase 1)

No secret value is reproduced here. Only the environment-variable **name** and the reason are listed.

## What was checked

| Check | Command (read-only) | Result |
|---|---|---|
| Secret files tracked by Git | `git ls-files --error-unmatch .env backend/.env backend/.env.qa backend/.env.qa-mongo` | None tracked (all four exist locally, all are ignored) |
| Ignore rules | `.gitignore` | `.env`, `.env.*`, `**/.env`, `!.env.example` — already correct |
| History contains live values | `qa_logs/secret_history_scan.txt` (produced earlier by `qa_secret_value_scan.py`, values never printed) | **Yes** — matches were found in `.env.example` revisions and in `backend/tests/test_phases_chat_docs.py` |
| `.env.example` placeholders | manual read | The MongoDB host `cluster0.mqn98sf...` is a real cluster name, not a placeholder |

## Conclusion

The working files are untracked **today**, but values with the same names were present in earlier
commits. Untracking a file does not remove what is already in the object store, so every credential
listed below must be considered **compromised** and rotated. History was **not** rewritten and
nothing was force-pushed; that decision stays with the repository owner.

## Credentials that must be rotated manually

| # | Variable (one or more of) | Provider | Why |
|---|---|---|---|
| 1 | `GEMINI_KEY_1..16`, `GEMINI_API_KEY`, `GEMINI_API_KEYS` | Google AI Studio | Key values appeared in committed history |
| 2 | `OPENROUTER_KEY_1..3`, `OPENROUTER_API_KEY_1..3` | OpenRouter | Same, plus primary chat provider |
| 3 | `GROQ_API_KEY_1..3` | Groq | Same |
| 4 | `HF_TOKEN`, `HF_TOKEN_1..2`, `HF_API_TOKEN`, `HF_TOKENS` | Hugging Face | Same |
| 5 | `TAVILY_API_KEY_1..2` (`Tailvy_API_Key_1..2`) | Tavily | Same |
| 6 | `CLOUDFLARE_ACCOUNT_ID` + `CLOUDFLARE_API_TOKEN` / `CloudFare_TOKEN_1..2` | Cloudflare | Account id is not a secret by itself; the token is |
| 7 | `LTX_API_KEY_1..2` / `LTX_TOKEN_1..2` | LTX | Same |
| 8 | `FAL_AI_TOKEN_1..2` / `FAL_KEY` | fal.ai | Same |
| 9 | `POLLINATIONS_API_KEY` | Pollinations | Same |
| 10 | `LIVEKIT_API_KEY` + `LIVEKIT_API_SECRET` | LiveKit Cloud | Same; rotating also invalidates minted room tokens (expected) |
| 11 | `MONGODB_URL` (password inside the URI) | MongoDB Atlas | The URI is printable from any log that leaked it; **rotate the database user password** and update the allow-list |
| 12 | `JWT_SECRET` | this application | Rotating signs every user out — do it together with the other rotations, once |
| 13 | `DEEPSEEK_API_KEY_1..2` | DeepSeek | Present in the root `.env` |
| 14 | `CLOUDINARY_API_SECRET` / `CLOUDINARY_API_KEY` | Cloudinary | Present in the root `.env` |

## Order of operations (safe sequence)

1. Create **new** keys at each provider; do not disable the old ones yet.
2. Put the new values only in the Render service environment / local `.env` files.
3. Deploy, verify `/api/v1/health`, a chat reply and one upload.
4. Disable the old keys at each provider.
5. Rotate `JWT_SECRET` last (it invalidates every session).
6. Rotate the Atlas password, update `MONGODB_URL` in the same deploy window.

## Not done automatically

* No `git filter-branch` / `filter-repo`, no force-push.
* No secret was printed, logged or written into this repository.
