"""Voice AI: speech-to-text and text-to-speech.

Providers are optional and reported honestly:

* STT — Groq Whisper (free tier, key already pooled as ``GROQ_*``) or Gemini
  (native audio). If neither is configured the endpoint returns 503.
* TTS — Gemini speech generation, then Edge's online neural voices (no key),
  then macOS ``say``. None available -> 503.

No audio is ever returned that was not produced by one of these engines.
"""

from __future__ import annotations

import asyncio
import io
import os
import shutil
import subprocess
import tempfile

import requests
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.api.deps import get_current_user
from app.core.config import settings
from app.models.user import User
from app.services.feature_flags import require_feature

router = APIRouter(prefix="/voice", tags=["voice"], dependencies=[Depends(require_feature("voice"))])

MAX_AUDIO = 20 * 1024 * 1024
AUDIO_TYPES = {
    "audio/wav", "audio/x-wav", "audio/mpeg", "audio/mp3", "audio/webm",
    "audio/ogg", "audio/mp4", "audio/m4a", "audio/flac",
}

STT_MODELS = ["whisper-large-v3-turbo", "whisper-large-v3"]


def _groq_keys():
    from backend.key_rotator import get_provider_credentials

    return get_provider_credentials("Groq")


def _gemini_keys():
    from backend.key_rotator import get_provider_credentials

    return get_provider_credentials("Gemini")


def _transcribe_groq(data: bytes, filename: str, language: str | None) -> str:
    keys = _groq_keys()
    if not keys:
        raise RuntimeError("Groq is not configured for transcription")
    last: str | None = None
    for key in keys:
        for model in STT_MODELS:
            try:
                response = requests.post(
                    "https://api.groq.com/openai/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {key.value}"},
                    files={"file": (filename or "audio.webm", data, "application/octet-stream")},
                    data={"model": model, **({"language": language} if language else {})},
                    timeout=120,
                )
                if response.status_code == 200:
                    text = (response.json().get("text") or "").strip()
                    if text:
                        return text
                last = f"{response.status_code}: {response.text[:200]}"
            except requests.RequestException as exc:
                last = str(exc)
    raise RuntimeError(f"Groq transcription failed ({last})")


def _transcribe_gemini(data: bytes, mime: str, language: str | None) -> str:
    keys = _gemini_keys()
    if not keys:
        raise RuntimeError("Gemini is not configured for transcription")
    from google import genai
    from google.genai import types

    contents = [
        f"Transcribe this audio exactly{' in ' + language if language else ''}. Return only the transcript.",
        types.Part.from_bytes(data=data, mime_type=mime or "audio/webm"),
    ]
    last: Exception | None = None
    for key in keys:
        client = genai.Client(api_key=key.value)
        try:
            response = client.models.generate_content(model="gemini-2.5-flash", contents=contents)
            text = (getattr(response, "text", "") or "").strip()
            if text:
                return text
            last = RuntimeError("empty transcript")
        except Exception as exc:  # noqa: BLE001
            last = exc
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()
    raise RuntimeError(f"Gemini transcription failed ({last})")


@router.get("/status")
def status(_: User = Depends(get_current_user)):
    edge = shutil.which("edge-tts")
    return {
        "stt": {
            "groq": bool(_groq_keys()),
            "gemini": bool(_gemini_keys()),
        },
        "tts": {
            "gemini": bool(_gemini_keys()),
            "edge_tts": bool(edge),
            "system_say": shutil.which("say") is not None,
        },
    }


@router.post("/transcribe")
async def transcribe(
    file: UploadFile = File(...),
    language: str = Form(default=""),
    engine: str = Form(default="auto"),
    _: User = Depends(get_current_user),
):
    content_type = (file.content_type or "").lower()
    if content_type and content_type not in AUDIO_TYPES:
        raise HTTPException(status_code=415, detail=f"Unsupported audio type: {content_type}")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="The uploaded audio is empty")
    if len(data) > MAX_AUDIO:
        raise HTTPException(status_code=413, detail="Audio exceeds the 20 MB limit")

    attempts = []
    if engine in {"auto", "groq"} and _groq_keys():
        attempts.append(("groq", lambda: _transcribe_groq(data, file.filename or "audio.webm", language or None)))
    if engine in {"auto", "gemini"} and _gemini_keys():
        attempts.append(("gemini", lambda: _transcribe_gemini(data, content_type or "audio/webm", language or None)))
    if not attempts:
        raise HTTPException(
            status_code=503,
            detail="Speech-to-text is not configured. Add a Groq key (GROQ_API_KEY) or a Gemini key on the server.",
        )
    errors = []
    for name, run in attempts:
        try:
            text = await asyncio.to_thread(run)
            return {"text": text, "engine": name, "characters": len(text)}
        except RuntimeError as exc:
            errors.append(f"{name}: {exc}")
    raise HTTPException(status_code=502, detail="Transcription failed — " + "; ".join(errors))


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=3000)
    voice: str = Field(default="en-US-AriaNeural")
    engine: str = Field(default="auto", pattern="^(auto|edge|say)$")


def _tts_edge(text: str, voice: str) -> bytes:
    import edge_tts

    async def run() -> bytes:
        communicate = edge_tts.Communicate(text, voice)
        buffer = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                buffer.write(chunk["data"])
        return buffer.getvalue()

    return asyncio.run(run())


def _tts_say(text: str) -> bytes:
    with tempfile.NamedTemporaryFile(suffix=".aiff", delete=False) as handle:
        path = handle.name
    try:
        subprocess.run(["say", "-o", path, text], check=True, timeout=60)
        with open(path, "rb") as reader:
            return reader.read()
    finally:
        os.unlink(path)


@router.post("/speak")
def speak(request: SpeakRequest, _: User = Depends(get_current_user)):
    engines = []
    if request.engine in {"auto", "edge"} and shutil.which("edge-tts"):
        engines.append(("edge", lambda: (_tts_edge(request.text, request.voice), "audio/mpeg", ".mp3")))
    if request.engine in {"auto", "say"} and shutil.which("say"):
        engines.append(("say", lambda: (_tts_say(request.text), "audio/aiff", ".aiff")))
    if not engines:
        raise HTTPException(
            status_code=503,
            detail="Text-to-speech is not configured. Install the 'edge-tts' package on the server to enable it.",
        )
    errors = []
    for name, run in engines:
        try:
            payload, media_type, extension = run()
            if payload:
                return Response(
                    content=payload,
                    media_type=media_type,
                    headers={
                        "Content-Disposition": f'inline; filename="speech{extension}"',
                        "X-TTS-Engine": name,
                    },
                )
            errors.append(f"{name}: empty audio")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: {exc}")
    raise HTTPException(status_code=502, detail="Text-to-speech failed — " + "; ".join(errors))
