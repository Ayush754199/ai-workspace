# AI Workspace v1.0 — Tester Test Cases

## Test instructions

Use a test account only. Do not paste or expose API keys in the browser, screenshots, bug reports, or chat messages. Testing must stay within the configured 20% test quota. Do not run bulk generation or repeated retry loops.

### Test setup

- Start the backend and frontend/Streamlit app using the project’s normal run commands.
- Confirm the tester has a normal user account and an administrator account.
- Confirm provider keys are present only in `.env` / `backend/.env`.
- Confirm paid video providers are disabled.
- Use small test media files: PNG/JPG under 5 MB and a short MP4 under 20 MB.
- Record browser, OS, timestamp, test case ID, and screenshot/video for every failure.

## A. Authentication and basic navigation

| ID | Test case | Steps | Expected result | Result |
|---|---|---|---|---|
| AUTH-01 | User login | Open app, enter valid test credentials, submit | User reaches workspace dashboard | ☐ Pass ☐ Fail |
| AUTH-02 | Invalid login | Enter wrong password and submit | Clear error appears; no login session is created | ☐ Pass ☐ Fail |
| AUTH-03 | Logout | Login, click Sign out, use browser Back | Protected workspace is not accessible without login | ☐ Pass ☐ Fail |
| NAV-01 | Workspace navigation | Open AI Chat, Video Generation, Video Editing, My Files, Settings | Each page loads without an exception or blank screen | ☐ Pass ☐ Fail |
| NAV-02 | Refresh persistence | Open a page, refresh the browser | Session and page load correctly; no duplicate widget/state error | ☐ Pass ☐ Fail |

## B. Chat AI and real-time response

| ID | Test case | Steps | Expected result | Result |
|---|---|---|---|---|
| CHAT-01 | Basic chat | Send: `Explain photosynthesis in 3 simple points.` | Response is relevant and displayed in the chat | ☐ Pass ☐ Fail |
| CHAT-02 | Streaming response | Send a prompt that produces a multi-paragraph answer | Text appears progressively; UI remains responsive | ☐ Pass ☐ Fail |
| CHAT-03 | Conversation context | Ask `My name is Tester.` then `What is my name?` | Second response uses the previous turn correctly | ☐ Pass ☐ Fail |
| CHAT-04 | OpenRouter free routing | Send a normal text prompt with OpenRouter enabled | Request uses an enabled free OpenRouter model; no paid model is selected | ☐ Pass ☐ Fail |
| CHAT-05 | Provider fallback | Temporarily disable the selected OpenRouter model from Admin, send a prompt | Next eligible provider/model is tried; user receives a response or clear availability error | ☐ Pass ☐ Fail |
| CHAT-06 | Live information | Ask a current query such as `What is the weather in Delhi today?` | Live context/source links appear when web research is enabled; no fabricated “live” claim when unavailable | ☐ Pass ☐ Fail |
| CHAT-07 | File quick action regression | Upload a supported document and use its chat/file action | No `too many values to unpack` error; file action completes or shows a useful error | ☐ Pass ☐ Fail |
| CHAT-08 | Widget state regression | Change a file/artifact selector, submit a chat message, then rerun/refresh | No `StreamlitWidgetAlreadyInstantiatedError` appears | ☐ Pass ☐ Fail |
| CHAT-09 | Chat quota | Send prompts until the configured test limit is reached | Chat quota is enforced; image/video quota counters are unchanged | ☐ Pass ☐ Fail |

## C. Image AI

| ID | Test case | Steps | Expected result | Result |
|---|---|---|---|---|
| IMG-01 | Text-to-image | Open Image AI, enter `A cinematic sunset over Jaipur, digital art`, generate | Image is returned and previewed; request is counted only against image quota | ☐ Pass ☐ Fail |
| IMG-02 | Image editing | Upload a valid JPG/PNG, enter `Make the background a soft blue gradient`, submit | Edited image is returned, or a clear “editing provider not configured/available” message is shown | ☐ Pass ☐ Fail |
| IMG-03 | Invalid image | Upload a non-image file or corrupted image | Request is rejected with a clear validation error; quota is not consumed | ☐ Pass ☐ Fail |
| IMG-04 | Provider fallback | Disable Cloudflare image provider and retry generation | Configured fallback is attempted; no silent success with an empty result | ☐ Pass ☐ Fail |
| IMG-05 | Image quota isolation | Generate one image, then check admin usage/quota | Image usage increases; chat and video usage do not increase | ☐ Pass ☐ Fail |

## D. Video Generation

| ID | Test case | Steps | Expected result | Result |
|---|---|---|---|---|
| VID-01 | Prompt-to-video | Open Video Generation → Prompt to Video; submit a short prompt | Job starts or completes with visible status/progress and a usable result | ☐ Pass ☐ Fail |
| VID-02 | Image-to-video | Open Video Generation → Image to Video; upload a valid image and prompt | Image-to-video path is used; it must not silently call text-to-video | ☐ Pass ☐ Fail |
| VID-03 | Empty prompt | Submit prompt-to-video with an empty prompt | Validation error appears; no provider call/quota deduction | ☐ Pass ☐ Fail |
| VID-04 | Invalid upload | Upload an invalid/oversized file to image-to-video | Clear file error appears; quota is released/not consumed | ☐ Pass ☐ Fail |
| VID-05 | Provider failure | Use an unavailable/warming provider or simulate provider failure | User sees `Video generation failed` with retry guidance; failed job is not marked completed | ☐ Pass ☐ Fail |
| VID-06 | Quota refund on failure | Trigger a video provider failure, then inspect video quota | Reserved quota is released; only successful generation consumes usage | ☐ Pass ☐ Fail |
| VID-07 | Video quota isolation | Generate a successful video and check usage | Video usage increases; chat/image counters remain unchanged | ☐ Pass ☐ Fail |
| VID-08 | Paid providers OFF | Check video capabilities/admin settings | Paid LTX/fal providers are not used unless an admin explicitly enables them | ☐ Pass ☐ Fail |
| VID-09 | Job refresh | Start a video job, refresh the page, open job history | Job status remains consistent and is not duplicated | ☐ Pass ☐ Fail |

## E. Video Editing

| ID | Test case | Steps | Expected result | Result |
|---|---|---|---|---|
| EDIT-01 | Trim/audio mode | Open Video Editing → Trim/Audio, upload short MP4, trim and export | Exported video is playable and audio remains correct | ☐ Pass ☐ Fail |
| EDIT-02 | Advanced editing location | Open Video Editing → Advanced Editing | Advanced timeline/effects functionality appears under Video Editing, not Video Generation | ☐ Pass ☐ Fail |
| EDIT-03 | Professional editor | Open Video Editing → Professional Editor | Timeline/editor loads without runtime error | ☐ Pass ☐ Fail |
| EDIT-04 | Unsupported media | Upload unsupported or corrupt video | Clear validation error; app remains usable | ☐ Pass ☐ Fail |

## F. Admin panel and routing controls

| ID | Test case | Steps | Expected result | Result |
|---|---|---|---|---|
| ADM-01 | Provider toggle | Admin → Providers; disable/enable a provider | Toggle persists after refresh and affects routing | ☐ Pass ☐ Fail |
| ADM-02 | Model toggle | Disable the selected model, submit a chat request | Disabled model is not selected | ☐ Pass ☐ Fail |
| ADM-03 | Provider priority | Change routing order to OpenRouter → Groq → Gemini | New order persists and fallback follows it | ☐ Pass ☐ Fail |
| ADM-04 | OpenRouter discovery | Run OpenRouter free-model discovery | Free models are listed without exposing API keys; unavailable/paid models are not enabled automatically | ☐ Pass ☐ Fail |
| ADM-05 | Chat quota control | Set a user chat daily and per-chat limit | New limits apply without restarting the app | ☐ Pass ☐ Fail |
| ADM-06 | Image/video quota control | Set image and video limits separately | Each limit saves independently and appears after refresh | ☐ Pass ☐ Fail |
| ADM-07 | Usage tracking | Generate one successful chat/image/video request | Correct capability/provider/model usage is recorded | ☐ Pass ☐ Fail |
| ADM-08 | Non-admin protection | Open admin endpoint/page using a normal user account | Access is denied; no admin data is returned | ☐ Pass ☐ Fail |

## G. Security and configuration checks

| ID | Test case | Steps | Expected result | Result |
|---|---|---|---|---|
| SEC-01 | Key secrecy | Inspect browser network requests and UI after generation | Provider API keys never appear in frontend payloads, errors, or logs | ☐ Pass ☐ Fail |
| SEC-02 | Env-only keys | Remove a provider key from `.env` in a test environment and restart | Provider becomes unavailable with a clear configuration message | ☐ Pass ☐ Fail |
| SEC-03 | Paid API guard | Keep paid providers disabled and submit video request | Paid provider is not called | ☐ Pass ☐ Fail |
| SEC-04 | Quota boundary | Reach the configured test quota, then make one more request | Request is rejected cleanly; no negative counters or partial reservation remain | ☐ Pass ☐ Fail |

## Bug report format

```text
Test case ID:
Build/commit:
Browser + version:
OS:
Date/time:
Steps to reproduce:
Expected result:
Actual result:
Screenshot/video:
Console/network error:
Was quota consumed? Yes/No/Unknown
Severity: Blocker / High / Medium / Low
```

## Tester sign-off

- Total passed: ____
- Total failed: ____
- Blockers: ____
- Quota stayed within 20% test limit: ☐ Yes ☐ No
- API keys remained in env files only: ☐ Yes ☐ No
- Tester name: ____________________
- Date: ____________________
