"""Auralis AI Workspace: secure multimodal Streamlit application."""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import logging
import os
import re
import secrets
import tempfile
import time
import uuid
from contextlib import contextmanager
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from html import escape
from inspect import currentframe
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import requests
import streamlit as st
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from bson import ObjectId
from bson.errors import InvalidId
from PIL import Image, ImageOps, UnidentifiedImageError
from bs4 import BeautifulSoup
from pymongo import MongoClient, ReturnDocument
from pymongo.collation import Collation
from pymongo.errors import DuplicateKeyError

from backend.auth_roles import is_admin_account
from backend.auth_security import (
    ADMIN_USERNAME as DEFAULT_ADMIN_USERNAME,
    PASSWORD_RESET_LOCK_MINUTES,
    PASSWORD_RESET_MAX_ATTEMPTS,
    SECURITY_QUESTIONS,
    is_admin_bootstrap_authorized,
    normalize_security_answer,
    validate_username,
)
from backend.mongo_rooms import (
    create_room,
    get_member_room,
    join_room,
    list_member_rooms,
    post_room_message,
    remove_room_member,
)
from backend.logger import log_connection_event, log_connection_failure
from backend.key_rotator import (
    ProviderCredential,
    ProviderRateLimitError,
    call_with_key_rotation,
    get_provider_credentials,
    get_next_hf_token,
)
from backend.video_editor import add_background_audio, trim_video
from backend.video_provider import generate_30s_video
from backend.advanced_editor import edit_video, transform_video_with_hugging_face
from backend.pro_video_editor import (
    ASPECT_PRESETS as PRO_ASPECT_PRESETS,
    BITRATE_PRESETS as PRO_BITRATES,
    BLUR_PRESETS as PRO_BLURS,
    CURVE_CHANNELS as PRO_CURVE_CHANNELS,
    EXPORT_FORMATS as PRO_EXPORT_FORMATS,
    FILTER_PRESETS as PRO_FILTERS,
    FPS_PRESETS as PRO_FPS,
    MASK_SHAPES as PRO_MASKS,
    RESOLUTION_PRESETS as PRO_RESOLUTIONS,
    TRANSITIONS as PRO_TRANSITIONS,
    VIDEO_SUFFIXES as PRO_VIDEO_SUFFIXES,
    AUDIO_SUFFIXES as PRO_AUDIO_SUFFIXES,
    AudioMixer,
    ColorGrade,
    OverlaySpec,
    ProEditError,
    ProEditSpec,
    TransformSpec,
    estimate_render_seconds,
    plan_operations,
    probe_video,
    render_pro_video,
    storyboard,
)
PRO_COLOR_PRESETS = ["Neutral", "Teal & Orange", "Film Noir", "Golden Hour", "Arctic", "Blockbuster"]
from config import (
    GEMINI_MODEL,
    GEMINI_MODEL_FALLBACKS,
    HF_VIDEO_TO_VIDEO_API_NAME,
    HF_VIDEO_TO_VIDEO_INPUTS_JSON,
    HF_VIDEO_TO_VIDEO_SPACE,
)

APP_TITLE = "Auralis AI Workspace"
DB_NAME = "enterprise_ai_db"
COLLECTION_NAME = "generations"
MAX_PROFILE_IMAGE_BYTES = 2 * 1024 * 1024
PROFILE_IMAGE_SIZE = (384, 384)
AUTH_SESSION_COOKIE = "auralis_auth_session"
AUTH_SESSION_TTL_DAYS = 30
PASSWORD_HASHER = PasswordHasher()
ADMIN_BOOTSTRAP_TOKEN = ""
ADMIN_USERNAME = DEFAULT_ADMIN_USERNAME
_USER_INDEXES_READY = False
_AUTH_SESSION_INDEXES_READY = False


def load_local_environment() -> None:
    for env_path in (Path(__file__).with_name(".env"), Path(__file__).with_name("backend") / ".env"):
        if not env_path.is_file():
            continue
        try:
            for raw_line in env_path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[7:].lstrip()
                name, separator, value = line.partition("=")
                if not separator:
                    continue
                name = name.strip()
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                    value = value[1:-1]
                if name and name not in os.environ:
                    os.environ[name] = value
        except OSError:
            continue


load_local_environment()
ADMIN_BOOTSTRAP_TOKEN = os.getenv("ADMIN_BOOTSTRAP_TOKEN", "").strip()
ADMIN_USERNAME = validate_username(
    os.getenv("ADMIN_USERNAME", DEFAULT_ADMIN_USERNAME).strip() or DEFAULT_ADMIN_USERNAME
)
MONGODB_URL = os.getenv("MONGODB_URL", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "PASTE_YOUR_ACTUAL_GEMINI_KEY_HERE")
# api-inference.huggingface.co was retired by Hugging Face; the provider now serves
# these models through router.huggingface.co (OpenAI-compatible chat completions).
HF_IMAGE_API = os.getenv("HF_IMAGE_API_URL", "https://router.huggingface.co/hf-inference/models/black-forest-labs/FLUX.1-schnell")
HF_VIDEO_API = os.getenv("HF_VIDEO_API_URL", "https://router.huggingface.co/hf-inference/models/damo-vilab/text-to-video-ms-1.7b")
HF_TEXT_API = os.getenv("HF_TEXT_API_URL", "https://router.huggingface.co/v1/chat/completions")
HF_TEXT_MODEL = os.getenv("HF_TEXT_MODEL", "meta-llama/Llama-3.1-8B-Instruct").strip()
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openrouter/free").strip() or "openrouter/free"
HF_TEXT_TO_VIDEO_SPACE = os.getenv("HF_TEXT_TO_VIDEO_SPACE", "").strip()
HF_IMAGE_TO_VIDEO_SPACE = os.getenv("HF_IMAGE_TO_VIDEO_SPACE", "").strip()
HF_STYLE_TRANSFER_SPACE = os.getenv("HF_STYLE_TRANSFER_SPACE", "").strip()
HF_TEXT_TO_VIDEO_API_NAME = os.getenv("HF_TEXT_TO_VIDEO_API_NAME", "").strip()
HF_IMAGE_TO_VIDEO_API_NAME = os.getenv("HF_IMAGE_TO_VIDEO_API_NAME", "").strip()
HF_STYLE_TRANSFER_API_NAME = os.getenv("HF_STYLE_TRANSFER_API_NAME", "").strip()
HF_TEXT_TO_VIDEO_INPUTS = os.getenv("HF_TEXT_TO_VIDEO_INPUTS", '["{prompt}"]')
HF_IMAGE_TO_VIDEO_INPUTS = os.getenv("HF_IMAGE_TO_VIDEO_INPUTS", '["{image}", "{prompt}"]')
HF_STYLE_TRANSFER_INPUTS = os.getenv("HF_STYLE_TRANSFER_INPUTS", '["{image}", "{prompt}"]')
SUPPORTED_FILES = [
    "pdf", "png", "jpg", "jpeg", "webp", "txt", "py", "js", "html", "css",
    "csv", "json", "md", "docx", "xlsx", "pptx",
]
ASPECT_RATIOS = {"1:1 (1080x1080)": (1080, 1080), "16:9 (1920x1080)": (1920, 1080), "9:16 (1080x1920)": (1080, 1920)}

st.set_page_config(page_title=APP_TITLE, page_icon="✦", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Space+Grotesk:wght@500;600;700&display=swap');
:root { color-scheme:dark; --bg:#0d0e15; --panel:rgba(22,24,38,.78); --panel2:#1a1c29; --line:rgba(148,163,184,.18); --text:#f8fafc; --muted:#9aa3bb; --violet:#8b5cf6; --cyan:#22d3ee; }
html, body, [class*="css"] { font-family:'DM Sans',sans-serif; }
.stApp { background:radial-gradient(circle at 75% 4%,rgba(124,58,237,.18),transparent 30rem),radial-gradient(circle at 8% 85%,rgba(6,182,212,.12),transparent 26rem),var(--bg); color:var(--text); }
[data-testid="stToast"] { position:fixed !important; top:1.1rem !important; right:1.1rem !important; bottom:auto !important; z-index:99999 !important; }
[data-testid="stToast"] > div { border:1px solid rgba(34,211,238,.5) !important; border-radius:14px !important; background:linear-gradient(145deg,rgba(24,25,42,.98),rgba(17,21,36,.98)) !important; box-shadow:0 14px 42px rgba(0,0,0,.4),0 0 24px rgba(34,211,238,.15) !important; backdrop-filter:blur(16px); }
[data-testid="stHeader"] { background:transparent; } [data-testid="stToolbar"] { visibility:hidden; }
[data-testid="stSidebar"] { background:linear-gradient(180deg,#0d0e15,#18182a 60%,#0d0e15); border-right:1px solid var(--line); }
[data-testid="stSidebar"][aria-expanded="true"] { width:240px !important; min-width:240px !important; max-width:240px !important; }
[data-testid="stSidebar"] * { color:var(--text); } [data-testid="stSidebar"] hr { border-color:var(--line); }
[data-testid="stSidebar"] button { border-radius:11px; border:1px solid var(--line); background:rgba(255,255,255,.04); }
[data-testid="stSidebar"] button:hover { border-color:var(--violet); box-shadow:0 0 18px rgba(139,92,246,.25); }
.block-container { max-width:1420px; padding:2rem 5vw 3rem; position:relative; z-index:1; }
.hero { padding:2rem 2.4rem; margin-bottom:1.3rem; border:1px solid rgba(139,92,246,.35); border-radius:24px; background:linear-gradient(135deg,rgba(28,27,56,.82),rgba(17,21,39,.7)); box-shadow:0 18px 70px rgba(0,0,0,.24),inset 0 1px rgba(255,255,255,.1); backdrop-filter:blur(16px); animation:rise .55s ease both; }
.hero h1 { margin:.25rem 0 0; font:700 clamp(2.2rem,4vw,4rem) 'Space Grotesk',sans-serif; letter-spacing:-.04em; } .hero h1 span { color:transparent; background:linear-gradient(100deg,var(--violet),var(--cyan)); background-clip:text; -webkit-background-clip:text; }
.eyebrow { margin:0; color:var(--cyan); font-size:.75rem; font-weight:700; letter-spacing:.16em; text-transform:uppercase; } .hero p { color:var(--muted); margin:.7rem 0 0; }
.chat-card { padding:1.1rem; border:1px solid var(--line); border-radius:20px; background:var(--panel); backdrop-filter:blur(16px); box-shadow:0 16px 50px rgba(0,0,0,.18); }
.st-key-sidebar_profile_block { position:sticky; bottom:0; z-index:5; margin-top:1rem; padding:.75rem; border:1px solid var(--line); border-radius:16px; background:linear-gradient(145deg,rgba(18,19,31,.98),rgba(25,24,43,.98)); box-shadow:0 -8px 26px rgba(0,0,0,.2); }
.st-key-sidebar_brand { padding:.35rem .25rem .75rem; border-bottom:1px solid var(--line); margin-bottom:.8rem; }
.st-key-sidebar_brand p { margin:0; color:var(--muted); font-size:.75rem; }
.st-key-sidebar_brand strong { color:var(--text); font:700 1rem 'Space Grotesk',sans-serif; letter-spacing:.02em; }
.st-key-nexus-chat-workspace { width:100%; }
.st-key-nexus-chat-header { padding:.45rem .2rem .65rem; margin-bottom:.35rem; border-bottom:1px solid var(--line); }
.st-key-nexus-chat-header h3 { margin:.15rem 0; font:600 1.05rem 'Space Grotesk',sans-serif; }
.st-key-nexus-chat-thread { padding:.2rem .45rem .7rem; }
.st-key-nexus-artifact-panel { position:sticky; top:.75rem; max-height:calc(100vh - 2rem); overflow-y:auto; padding:.9rem; border:1px solid rgba(34,211,238,.25); border-radius:18px; background:linear-gradient(160deg,rgba(18,20,33,.88),rgba(13,15,27,.84)); box-shadow:0 16px 50px rgba(0,0,0,.22); backdrop-filter:blur(16px); }
.artifact-panel-heading { color:var(--cyan); font-size:.7rem; font-weight:700; letter-spacing:.14em; text-transform:uppercase; }
.artifact-meta { color:var(--muted); font-size:.75rem; }
.st-key-nexus-chat-workspace [data-testid="stChatMessage"] { max-width:100%; }
.st-key-nexus-chat-workspace [data-testid="stChatInput"] { position:relative; bottom:auto; }
.st-key-nexus-chat-workspace .st-key-unified_chat_bar { position:sticky; bottom:.35rem; }
.st-key-unified_chat_bar { position:sticky; bottom:.5rem; z-index:20; padding:.75rem 1rem .25rem; border:1px solid rgba(139,92,246,.42); border-radius:20px; background:linear-gradient(145deg,rgba(18,19,31,.97),rgba(25,24,43,.96)); box-shadow:0 14px 45px rgba(0,0,0,.42),0 0 28px rgba(124,58,237,.14); backdrop-filter:blur(20px); }
.st-key-unified_chat_bar [data-testid="stChatInput"] { position:relative; bottom:auto; padding:0; border:0; background:transparent !important; box-shadow:none; }
.st-key-unified_chat_bar [data-testid="stChatInput"] > div { background:#171827 !important; border:1px solid rgba(148,163,184,.2) !important; border-radius:14px !important; }
.st-key-unified_chat_bar [data-testid="stFileUploader"] { padding:0; border:0; background:transparent; }
.st-key-unified_chat_bar [data-testid="stFileUploaderDropzone"] { min-height:2.5rem; padding:.15rem .4rem; border:1px dashed rgba(34,211,238,.35); border-radius:12px; background:rgba(20,21,35,.72); }
.st-key-unified_chat_bar [data-testid="stAudioInput"] { min-height:2.5rem; padding:.15rem .4rem; }
.st-key-unified_chat_bar [data-testid="stAudioInput"] label { display:none; }
.st-key-unified_chat_bar [data-testid="stAudioInput"] > div { min-height:2.4rem; }
.st-key-unified_chat_bar [data-testid="stSelectbox"] { margin-top:.15rem; }
.auralis-dialog { padding:1.1rem; border:1px solid rgba(139,92,246,.4); border-radius:18px; background:linear-gradient(145deg,rgba(24,25,42,.98),rgba(17,21,36,.98)); box-shadow:0 18px 55px rgba(0,0,0,.4),inset 0 1px rgba(255,255,255,.08); }
.auralis-dialog h3 { margin:.15rem 0 .45rem; }
.auralis-dialog-success { border-color:rgba(34,211,238,.5); }
.auralis-dialog-error { border-color:rgba(248,113,113,.58); }
.attachment-preview { display:flex; align-items:center; gap:.45rem; min-height:2rem; padding:.2rem .55rem; border:1px solid rgba(34,211,238,.28); border-radius:999px; background:rgba(34,211,238,.08); color:#dbeafe; font-size:.8rem; }
[data-testid="stPopover"] button { border-radius:11px; background:rgba(139,92,246,.16); border:1px solid rgba(139,92,246,.4); color:var(--text); }
[data-testid="stChatMessage"] { background:rgba(29,31,49,.78); border:1px solid var(--line); border-radius:17px; color:var(--text); margin:.7rem 0; }
[data-testid="stBottom"], [data-testid="stBottom"] > div { background:var(--bg); }
[data-testid="stChatInput"] { border:1px solid rgba(139,92,246,.45); background:rgba(20,21,35,.9) !important; box-shadow:0 0 28px rgba(124,58,237,.16); }
[data-testid="stChatInput"] textarea { background:transparent !important; color:var(--text) !important; }
div[data-baseweb="select"] > div, div[data-baseweb="input"] > div, div[data-baseweb="textarea"] > div { background-color:var(--panel2) !important; border-color:var(--line) !important; color:var(--text) !important; }
[data-testid="stSelectbox"] [data-baseweb="select"], [data-testid="stSelectbox"] [data-baseweb="select"] > div, [data-testid="stSelectbox"] [role="combobox"] { background:var(--panel2) !important; background-color:var(--panel2) !important; border-color:rgba(139,92,246,.45) !important; color:var(--text) !important; }
[data-testid="stSelectbox"] [data-baseweb="select"] *, [data-testid="stSelectbox"] [role="combobox"] * { color:var(--text) !important; }
[data-testid="stSelectbox"] svg { fill:var(--muted) !important; }
div[data-baseweb="select"] input, input, textarea { color:var(--text) !important; }
[data-testid="stFileUploader"] { border:1px dashed rgba(34,211,238,.35); border-radius:14px; background:rgba(15,16,29,.5); padding:.3rem; }
[data-testid="stFileUploaderDropzone"] { background:rgba(20,21,35,.9); border-color:rgba(34,211,238,.35); }
[data-testid="stFileUploaderDropzone"] * { color:var(--text) !important; }
[data-testid="stFileUploaderDropzone"] button { background:var(--panel2) !important; border:1px solid var(--line) !important; }
[data-testid="stAudioInput"], [data-testid="stAudioInput"] > div { background:var(--panel2) !important; background-color:var(--panel2) !important; border:1px solid rgba(139,92,246,.4) !important; border-radius:14px !important; color:var(--text) !important; box-shadow:0 0 24px rgba(124,58,237,.12); }
[data-testid="stAudioInput"] div, [data-testid="stAudioInput"] span, [data-testid="stAudioInput"] p { background-color:transparent !important; color:var(--text) !important; }
[data-testid="stAudioInput"] button, [data-testid="stAudioInput"] [role="button"] { background:rgba(139,92,246,.2) !important; background-color:rgba(139,92,246,.2) !important; border:1px solid rgba(139,92,246,.45) !important; border-radius:10px !important; color:var(--text) !important; }
[data-testid="stAudioInput"] svg { color:var(--text) !important; fill:var(--text) !important; }
[data-testid="stAudioInput"] input, [data-testid="stAudioInput"] audio { background:var(--panel2) !important; color:var(--text) !important; color-scheme:dark; }
[data-baseweb="popover"], [role="listbox"] { background:var(--panel2) !important; color:var(--text) !important; }
[role="option"]:hover { background:rgba(139,92,246,.22) !important; }
button[kind="primary"], .stButton > button { border:0; border-radius:11px; background:linear-gradient(110deg,var(--violet),#4f46e5); color:white; font-weight:700; box-shadow:0 0 22px rgba(124,58,237,.28); }
button[kind="primary"]:hover, .stButton > button:hover { transform:translateY(-2px); box-shadow:0 0 34px rgba(34,211,238,.28); }
[data-testid="stImage"] img, video { border-radius:15px; border:1px solid rgba(34,211,238,.26); }
.history-card { padding:1rem; min-height:110px; border:1px solid var(--line); border-radius:15px; background:rgba(25,27,43,.76); transition:transform .25s,border-color .25s; } .history-card:hover { transform:translateY(-4px); border-color:var(--cyan); }
.badge { display:inline-block; padding:.25rem .55rem; border-radius:99px; background:rgba(139,92,246,.2); color:#c4b5fd; font-size:.72rem; font-weight:700; }
@keyframes rise { from { opacity:0; transform:translateY(12px); } to { opacity:1; transform:translateY(0); } } @media(max-width:700px){.block-container{padding:1.2rem .7rem 2rem}.hero{padding:1.5rem}.hero h1{font-size:2.35rem}}
@media(max-width:900px) {
  .st-key-nexus-artifact-panel { position:relative; top:auto; max-height:none; margin-top:1rem; }
  .st-key-nexus-chat-thread { padding-inline:0; }
}
.stApp > canvas, [data-testid="stAppViewContainer"] > canvas, .stApp::before, .stApp::after { pointer-events:none !important; z-index:-1 !important; }
[data-testid="stForm"], [data-testid="stTextInput"], [data-testid="stTextInput"] input, [data-testid="stFormSubmitButton"], [data-testid="stFormSubmitButton"] button, [data-testid="stForm"] button { position:relative; pointer-events:auto !important; z-index:9999 !important; }
</style>
""", unsafe_allow_html=True)

st.markdown("""
<style>
:root {
  --nexus-accent: #8b5cf6;
  --nexus-accent-rgb: 139,92,246;
}
[data-testid="stSidebar"] {
  background:linear-gradient(180deg,rgba(13,14,21,.94),rgba(24,24,42,.86),rgba(13,14,21,.94)) !important;
  backdrop-filter:blur(18px);
}
[data-testid="stForm"], [data-testid="stVerticalBlockBorderWrapper"],
[data-testid="stExpander"], [data-testid="stPopover"] {
  border-color:rgba(var(--nexus-accent-rgb),.22) !important;
  border-radius:16px !important;
  background:rgba(20,22,36,.48);
  backdrop-filter:blur(12px);
}
div[data-baseweb="input"] > div, div[data-baseweb="textarea"] > div,
[data-testid="stSelectbox"] [data-baseweb="select"] > div {
  background:rgba(26,28,41,.72) !important;
  border:1px solid rgba(var(--nexus-accent-rgb),.3) !important;
  border-radius:12px !important;
  transition:border-color .18s ease,box-shadow .18s ease;
}
div[data-baseweb="input"] > div:focus-within,
div[data-baseweb="textarea"] > div:focus-within,
[data-testid="stSelectbox"] [data-baseweb="select"]:focus-within {
  border-color:rgba(var(--nexus-accent-rgb),.8) !important;
  box-shadow:0 0 0 2px rgba(var(--nexus-accent-rgb),.12) !important;
}
.stButton > button, [data-testid="stFormSubmitButton"] button,
[data-testid="stDownloadButton"] button {
  border:1px solid rgba(var(--nexus-accent-rgb),.38);
  border-radius:12px;
  background:linear-gradient(135deg,rgba(var(--nexus-accent-rgb),.24),rgba(24,26,42,.78));
  backdrop-filter:blur(10px);
  transition:transform .18s ease,border-color .18s ease,box-shadow .18s ease;
}
.stButton > button:hover, [data-testid="stFormSubmitButton"] button:hover,
[data-testid="stDownloadButton"] button:hover {
  transform:translateY(-1px);
  border-color:rgba(var(--nexus-accent-rgb),.8);
  box-shadow:0 0 22px rgba(var(--nexus-accent-rgb),.2);
}
.nexus-brand {
  padding:1.35rem 1.7rem;
  margin:0 0 1.15rem;
  border:1px solid rgba(var(--nexus-accent-rgb),.34);
  border-radius:22px;
  background:linear-gradient(120deg,rgba(22,24,40,.84),rgba(20,22,36,.52));
  box-shadow:0 14px 44px rgba(0,0,0,.2),inset 0 1px rgba(255,255,255,.07);
  backdrop-filter:blur(16px);
}
.nexus-brand h1 {
  margin:0;
  font:700 clamp(1.8rem,3vw,2.7rem) 'Space Grotesk',sans-serif;
  letter-spacing:-.04em;
}
.nexus-brand p { margin:.35rem 0 0; color:#aab1c8; }
.nexus-processing-pulse {
  position:relative;
  width:100%;
  height:3px;
  margin:.25rem 0 .6rem;
  border-radius:999px;
  pointer-events:none !important;
  background:linear-gradient(90deg,transparent,rgba(var(--nexus-accent-rgb),.2),rgba(var(--nexus-accent-rgb),.95),rgba(var(--nexus-accent-rgb),.2),transparent);
  background-size:200% 100%;
  animation:nexus-pulse 1.8s linear infinite;
}
.nexus-preview {
  min-height:300px;
  padding:1rem;
  border:1px solid rgba(var(--nexus-accent-rgb),.3);
  border-radius:18px;
  background:rgba(18,20,33,.52);
  backdrop-filter:blur(14px);
}
[data-testid="stDialog"] {
  width:min(96vw,1600px) !important;
  max-width:96vw !important;
}
[data-testid="stDialog"] video, [data-testid="stDialog"] img {
  max-height:78vh;
  object-fit:contain;
}
@keyframes nexus-drift {
  from { transform:translate3d(-.5%,0,0) scale(1.02); }
  to { transform:translate3d(.5%,-.5%,0) scale(1.04); }
}
@keyframes nexus-pulse { from { background-position:100% 0; opacity:.55; } to { background-position:-100% 0; opacity:1; } }
@media (prefers-reduced-motion:reduce) {
  .stApp::before, .nexus-processing-pulse { animation:none !important; }
  *, *::before, *::after { scroll-behavior:auto !important; transition-duration:.01ms !important; }
}
@media(max-width:760px) {
  .nexus-brand { padding:1.1rem; border-radius:17px; }
  .nexus-preview { min-height:180px; }
  .st-key-nexus-image-workspace [data-testid="stHorizontalBlock"],
  .st-key-nexus-video-workspace [data-testid="stHorizontalBlock"],
  .st-key-nexus-media-workspace [data-testid="stHorizontalBlock"],
  .st-key-nexus-advanced-workspace [data-testid="stHorizontalBlock"] {
    flex-direction:column !important;
  }
  .st-key-nexus-image-workspace [data-testid="column"],
  .st-key-nexus-video-workspace [data-testid="column"],
  .st-key-nexus-media-workspace [data-testid="column"],
  .st-key-nexus-advanced-workspace [data-testid="column"] {
    width:100% !important;
    flex:1 1 100% !important;
  }
}
</style>
""", unsafe_allow_html=True)

THEME_CONFIG: dict[str, dict[str, str]] = {
    "nebula": {
        "accent": "139,92,246",
        "pattern": "radial-gradient(ellipse at 18% 22%,rgba(86,65,191,.3),transparent 45%),radial-gradient(ellipse at 78% 72%,rgba(24,177,197,.16),transparent 42%),radial-gradient(circle at 50% 48%,rgba(255,255,255,.06) 1px,transparent 2px)",
        "brand": "NEXUS Cipher",
        "subtitle": "A calm deep-space workspace for ideas, files, and conversations.",
    },
    "cosmic": {
        "accent": "34,211,238",
        "pattern": "radial-gradient(ellipse at 50% 42%,rgba(34,211,238,.18),transparent 40%),repeating-radial-gradient(ellipse at 50% 55%,transparent 0 42px,rgba(34,211,238,.09) 43px 44px),radial-gradient(circle at 72% 24%,rgba(139,92,246,.2),transparent 34%)",
        "brand": "NEXUS Aether",
        "subtitle": "Shape prompts into cinematic motion across a spacetime grid.",
    },
    "hologram": {
        "accent": "45,212,191",
        "pattern": "radial-gradient(ellipse at 22% 30%,rgba(45,212,191,.2),transparent 38%),radial-gradient(ellipse at 75% 62%,rgba(217,70,239,.18),transparent 42%),radial-gradient(ellipse at 45% 88%,rgba(59,130,246,.15),transparent 38%)",
        "brand": "NEXUS Aura",
        "subtitle": "Explore image generation through a fluid holographic lens.",
    },
    "waveform": {
        "accent": "244,114,182",
        "pattern": "repeating-linear-gradient(112deg,transparent 0 22px,rgba(244,114,182,.12) 23px 24px,transparent 25px 55px),radial-gradient(ellipse at 60% 48%,rgba(139,92,246,.2),transparent 50%)",
        "brand": "NEXUS Aether",
        "subtitle": "Tune, trim, and transform footage on the editing timeline.",
    },
    "matrix": {
        "accent": "52,211,153",
        "pattern": "repeating-linear-gradient(180deg,transparent 0 28px,rgba(52,211,153,.08) 29px 30px,transparent 31px 58px),radial-gradient(ellipse at 25% 35%,rgba(16,185,129,.16),transparent 42%),radial-gradient(ellipse at 75% 70%,rgba(34,211,238,.12),transparent 40%)",
        "brand": "NEXUS Cipher",
        "subtitle": "Connect ideas and conversations across a neural mesh.",
    },
    "radar": {
        "accent": "251,191,36",
        "pattern": "repeating-radial-gradient(circle at 78% 30%,transparent 0 34px,rgba(251,191,36,.1) 35px 36px),radial-gradient(ellipse at 78% 30%,rgba(251,191,36,.12),transparent 46%),linear-gradient(135deg,transparent 48%,rgba(34,211,238,.08) 49%,transparent 50%)",
        "brand": "NEXUS Omni",
        "subtitle": "A command-radar view for workspace administration.",
    },
}


def set_active_theme(nav: str, prompt: str = "") -> str:
    """Resolve the current visual state from navigation and optional prompt context."""
    normalized_nav = nav.casefold()
    theme = "nebula"
    if normalized_nav in {"videos", "video"}:
        theme = "cosmic"
    elif normalized_nav in {"images", "image"}:
        theme = "hologram"
    elif normalized_nav in {"media", "advanced studio", "advanced video editing studio"}:
        theme = "waveform"
    elif normalized_nav in {"customize", "admin", "admin panel"}:
        theme = "radar"
    elif normalized_nav in {"new chat", "library", "group chat", "chat"}:
        theme = "matrix"

    text = prompt.casefold()
    if text and theme == "matrix":
        if any(word in text for word in ("space", "cosmic", "galaxy", "nebula", "planet", "orbit")):
            theme = "cosmic"
        elif any(word in text for word in ("ai", "cyber", "code", "matrix", "neural", "robot")):
            theme = "matrix"
        elif any(word in text for word in ("art", "creative", "paint", "hologram", "color", "design")):
            theme = "hologram"
    st.session_state["active_theme"] = theme
    return theme


def render_active_theme() -> None:
    theme = THEME_CONFIG.get(st.session_state.get("active_theme", "nebula"), THEME_CONFIG["nebula"])
    st.markdown(
        f"<style>.stApp{{--nexus-accent:{theme['accent']};--nexus-accent-rgb:{theme['accent']};"
        f"background-image:{theme['pattern']},radial-gradient(circle at 75% 4%,rgba(124,58,237,.18),transparent 30rem),"
        f"radial-gradient(circle at 8% 85%,rgba(6,182,212,.12),transparent 26rem) !important;"
        "background-attachment:fixed !important;}</style>",
        unsafe_allow_html=True,
    )


def render_brand_header() -> None:
    theme = THEME_CONFIG.get(st.session_state.get("active_theme", "nebula"), THEME_CONFIG["nebula"])
    st.markdown(
        f"<header class='nexus-brand'><h1>{theme['brand']}</h1>"
        f"<p>{theme['subtitle']}</p></header>",
        unsafe_allow_html=True,
    )


@contextmanager
def processing_pulse():
    pulse = st.empty()
    pulse.markdown("<div class='nexus-processing-pulse' aria-hidden='true'></div>", unsafe_allow_html=True)
    try:
        yield
    finally:
        pulse.empty()


def init_state() -> None:
    defaults = {
        "messages": [],
        "chat_title": "New Chat",
        "chat_id": str(uuid.uuid4()),
        "nav": "New chat",
        "model": "Flash",
        "last_media": None,
        "media_result": None,
        "advanced_video_result": None,
        "advanced_video_upload_nonce": 0,
        "advanced_audio_upload_nonce": 0,
        "advanced_ai_upload_nonce": 0,
        "pro_media_nonce": 0,
        "pro_render_info": None,
        "pro_render_log": [],
        "pro_titles": [],
        "pro_source_bytes": None,
        "pro_source_name": None,
        "pro_rendered": None,
        "pro_scale_x": 100,
        "pro_scale_y": 100,
        "pro_position_x": 0,
        "pro_position_y": 0,
        "pro_rotate": 0,
        "pro_aspect_ratio": "Original",
        "pro_export_aspect": "Original",
        "pro_scale_mode": "fit",
        "pro_crop_top": 0,
        "pro_crop_right": 0,
        "pro_crop_bottom": 0,
        "pro_crop_left": 0,
        "pro_stabilize": False,
        "pro_motion_tracking": False,
        "pro_source_volume": 1.0,
        "pro_music_volume": 0.7,
        "pro_voiceover_volume": 1.0,
        "pro_sfx_volume": 0.8,
        "pro_master_volume": 1.0,
        "pro_music_ducking": True,
        "pro_noise_reduction": 0.0,
        "pro_normalize": True,
        "pro_color_preset": "Neutral",
        "pro_exposure": 0,
        "pro_contrast": 0,
        "pro_saturation": 0,
        "pro_vibrance": 0,
        "pro_temperature": 0,
        "pro_tint": 0,
        "pro_gamma": 0,
        "pro_lift": 0,
        "pro_gain": 0,
        "pro_shadows_hue": 0,
        "pro_midtones_hue": 0,
        "pro_highlights_hue": 0,
        "pro_curve_channel": "master",
        "pro_speed": 1.0,
        "pro_reverse": False,
        "pro_freeze_at": 0.0,
        "pro_freeze_seconds": 0.0,
        "pro_fade_in": 0.0,
        "pro_fade_out": 0.0,
        "pro_transition": "None",
        "pro_auto_captions_on": False,
        "pro_captions_text": "",
        "pro_background_removal": False,
        "pro_chroma_key": "#00ff00",
        "pro_chroma_tolerance": 0.2,
        "pro_face_blur": False,
        "pro_silence_removal": False,
        "pro_filter_preset": "None",
        "pro_filter_strength": 1.0,
        "pro_blur": "None",
        "pro_blur_radius": 0.0,
        "pro_mask_shape": "None",
        "pro_mask_feather": 0.2,
        "pro_background_fill": "#05060a",
        "pro_export_format": "MP4",
        "pro_resolution": "1080p",
        "pro_fps": "30",
        "pro_bitrate": "High (Recommended)",
        "pro_quality": 80.0,
        "pro_watermark_text": "",
        "pro_watermark_opacity": 0.6,
        "pro_watermark_position": "bottom-right",
        "file_input_nonce": 0,
        "audio_input_nonce": 0,
        "profile_upload_nonce": 0,
        "active_theme": "nebula",
        "last_context_prompt": "",
        "artifact_panel_open": True,
        "artifact_files": [],
        "artifact_source": "Latest response",
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def set_feedback(
    kind: str,
    title: str,
    message: str,
    *,
    details: str | Exception = "",
    transaction: dict[str, Any] | None = None,
) -> None:
    provider_error = details if isinstance(details, Exception) else None
    while provider_error is not None and not isinstance(provider_error, ProviderRateLimitError):
        provider_error = provider_error.__cause__ or provider_error.__context__
    if isinstance(provider_error, ProviderRateLimitError):
        provider_name = provider_error.provider
        is_hugging_face = provider_name == "Hugging Face"
        title = "Hugging Face GPU Busy" if is_hugging_face else "Gemini Quota Reached"
        message = (
            f"{provider_name} rejected requests for key {provider_error.key_id}. "
            f"Wait about {provider_error.cooldown_seconds} seconds before trying again. "
            "You can add backup keys or choose a lighter model preset. Daily or project quotas may require "
            "checking the provider dashboard for the actual reset time."
        )
        details = provider_error.diagnostics or str(provider_error)
        transaction = {
            **(transaction or {}),
            "provider": provider_name,
            "key_id": provider_error.key_id,
            "status_code": provider_error.status_code,
            "suggested_cooldown_seconds": provider_error.cooldown_seconds,
        }
    elif isinstance(details, Exception):
        details = str(details)
    st.session_state["feedback_dialog"] = {
        "kind": kind,
        "title": title,
        "message": message,
        "details": details,
        "transaction": transaction or {},
    }


@st.dialog("Auralis · Status", width="small")
def feedback_dialog() -> None:
    feedback = st.session_state.get("feedback_dialog")
    if not feedback:
        return
    kind = feedback.get("kind", "error")
    title = escape(str(feedback.get("title", "Request status")))
    message = escape(str(feedback.get("message", "")))
    with st.container(border=True):
        st.markdown(
            f"<div class='auralis-dialog auralis-dialog-{kind}'>"
            f"<h3>{title}</h3>"
            f"<p>{message}</p></div>",
            unsafe_allow_html=True,
        )
        transaction = feedback.get("transaction") or {}
        if transaction:
            pass
        details = feedback.get("details", "")
        if details and kind != "success":
            with st.expander("Why did this fail?"):
                st.code(details, language="json" if details.lstrip().startswith("{") else "text")
    button_label = "Continue to Workspace" if kind == "success" else "OK"
    if st.button(button_label, key="feedback_continue", use_container_width=True):
        st.session_state.pop("feedback_dialog", None)
        st.rerun()


def render_pending_feedback() -> None:
    if st.session_state.get("feedback_dialog"):
        feedback_dialog()


def set_connection_failure(
    error: Exception,
    *,
    endpoint: str,
    payload: Any,
    title: str,
    message: str,
) -> None:
    frame = currentframe()
    try:
        caller = frame.f_back.f_code.co_name if frame and frame.f_back else None
    finally:
        del frame
    details = log_connection_failure(error, endpoint=endpoint, payload=payload, function=caller)
    set_feedback("error", title, message, details=details)


@st.cache_resource(show_spinner=False)
def get_mongo_client():
    mongo_url = MONGODB_URL.strip()
    if not mongo_url:
        try:
            mongo_url = str(st.secrets.get("MONGODB_URL", "")).strip()
        except Exception:
            mongo_url = ""
    if not mongo_url:
        error = RuntimeError("MongoDB connection is not configured. Set MONGODB_URL in the local environment.")
        details = log_connection_failure(
            error,
            endpoint="mongodb://Atlas",
            payload={"configured": False, "database": DB_NAME},
        )
        raise RuntimeError(f"{error}\nDiagnostics: {details}") from error
    try:
        client = MongoClient(mongo_url, serverSelectionTimeoutMS=5000)
        client.admin.command("ping")
        return client
    except Exception as exc:
        details = log_connection_failure(
            exc,
            endpoint="mongodb://Atlas",
            payload={"database": DB_NAME, "operation": "ping"},
        )
        cause = exc
        while getattr(cause, "__cause__", None):
            cause = cause.__cause__
        if getattr(cause, "code", None) in {18, 8000} or "authentication failed" in str(cause).lower():
            raise RuntimeError(
                "MongoDB Atlas rejected the database credentials. In Atlas > Database Access, reset the "
                "database user's password (this is not your Atlas website password), grant readWrite on "
                "enterprise_ai_db, URL-encode special password characters such as # as %23 in MONGODB_URL, "
                f"then restart Auralis.\nDiagnostics: {details}"
            ) from exc
        raise RuntimeError(
            f"MongoDB Atlas connection failed. Check MONGODB_URL and network access.\nDiagnostics: {details}"
        ) from exc


def get_collection():
    return get_mongo_client()[DB_NAME][COLLECTION_NAME]


def get_users_collection():
    global _USER_INDEXES_READY
    users = get_mongo_client()[DB_NAME]["users"]
    if not _USER_INDEXES_READY:
        try:
            users.create_index(
                [("username", 1)],
                name="username_case_insensitive_unique",
                unique=True,
                collation=Collation(locale="en", strength=2),
                partialFilterExpression={"username": {"$type": "string"}},
            )
            _USER_INDEXES_READY = True
        except Exception as exc:
            log_connection_failure(
                exc,
                endpoint="mongodb://Atlas",
                payload={
                    "collection": "users",
                    "operation": "create_index",
                    "index": "username_case_insensitive_unique",
                },
            )
            raise RuntimeError(
                "Could not enforce unique usernames. Resolve any existing duplicate usernames in "
                "enterprise_ai_db.users before creating accounts."
            ) from exc
    return users


def get_auth_sessions_collection():
    global _AUTH_SESSION_INDEXES_READY
    sessions = get_mongo_client()[DB_NAME]["user_sessions"]
    if not _AUTH_SESSION_INDEXES_READY:
        try:
            sessions.create_index(
                "expires_at",
                name="user_sessions_expiry",
                expireAfterSeconds=0,
            )
            sessions.create_index("user_id", name="user_sessions_user_id")
            _AUTH_SESSION_INDEXES_READY = True
        except Exception as exc:
            log_connection_failure(
                exc,
                endpoint="mongodb://Atlas",
                payload={"collection": "user_sessions", "operation": "create_index"},
            )
            raise RuntimeError("Could not initialize persistent sign-in sessions.") from exc
    return sessions


def _ensure_collection_indexes(collection, indexes):
    for index in indexes:
        try:
            collection.create_index(index["keys"], name=index["name"], **index.get("options", {}))
        except Exception as exc:
            log_connection_failure(
                exc,
                endpoint="mongodb://Atlas",
                payload={"collection": collection.name, "operation": "create_index", "index": index["name"]},
            )
            raise


def get_conversations_collection():
    conversations = get_mongo_client()[DB_NAME]["conversations"]
    _ensure_collection_indexes(
        conversations,
        [
            {"keys": [("user_id", 1), ("updated_at", -1)], "name": "conversations_user_updated"},
            {"keys": [("conversation_id", 1)], "name": "conversations_id", "options": {"unique": True}},
        ],
    )
    return conversations


def get_conversation_messages_collection():
    messages = get_mongo_client()[DB_NAME]["conversation_messages"]
    _ensure_collection_indexes(
        messages,
        [
            {"keys": [("conversation_id", 1), ("created_at", 1)], "name": "messages_conversation_created"},
            {"keys": [("user_id", 1), ("created_at", -1)], "name": "messages_user_created"},
        ],
    )
    return messages


def get_conversation_summaries_collection():
    summaries = get_mongo_client()[DB_NAME]["conversation_summaries"]
    _ensure_collection_indexes(
        summaries,
        [
            {"keys": [("conversation_id", 1)], "name": "summaries_conversation", "options": {"unique": True}},
            {"keys": [("user_id", 1), ("updated_at", -1)], "name": "summaries_user_updated"},
        ],
    )
    return summaries


def get_user_memories_collection():
    memories = get_mongo_client()[DB_NAME]["user_memories"]
    _ensure_collection_indexes(
        memories,
        [
            {"keys": [("user_id", 1), ("memory_key", 1)], "name": "memories_user_key", "options": {"unique": True}},
            {"keys": [("user_id", 1), ("updated_at", -1)], "name": "memories_user_updated"},
        ],
    )
    return memories


def get_research_sessions_collection():
    sessions = get_mongo_client()[DB_NAME]["research_sessions"]
    _ensure_collection_indexes(
        sessions,
        [
            {"keys": [("research_id", 1)], "name": "research_sessions_id", "options": {"unique": True}},
            {"keys": [("user_id", 1), ("created_at", -1)], "name": "research_sessions_user_created"},
        ],
    )
    return sessions


def get_research_sources_collection():
    sources = get_mongo_client()[DB_NAME]["research_sources"]
    _ensure_collection_indexes(
        sources,
        [
            {"keys": [("research_id", 1), ("created_at", 1)], "name": "research_sources_research_created"},
            {"keys": [("user_id", 1), ("created_at", -1)], "name": "research_sources_user_created"},
        ],
    )
    return sources


def get_tool_execution_logs_collection():
    logs = get_mongo_client()[DB_NAME]["tool_execution_logs"]
    _ensure_collection_indexes(
        logs,
        [
            {"keys": [("user_id", 1), ("created_at", -1)], "name": "tool_logs_user_created"},
            {"keys": [("conversation_id", 1), ("created_at", -1)], "name": "tool_logs_conversation_created"},
        ],
    )
    return logs


def get_provider_usage_collection():
    usage = get_mongo_client()[DB_NAME]["provider_usage"]
    _ensure_collection_indexes(
        usage,
        [
            {"keys": [("user_id", 1), ("created_at", -1)], "name": "provider_usage_user_created"},
            {"keys": [("provider", 1), ("created_at", -1)], "name": "provider_usage_provider_created"},
        ],
    )
    return usage


def get_user_feature_permissions_collection():
    permissions = get_mongo_client()[DB_NAME]["user_feature_permissions"]
    _ensure_collection_indexes(
        permissions,
        [
            {"keys": [("user_id", 1), ("feature", 1)], "name": "feature_permissions_user_feature", "options": {"unique": True}},
        ],
    )
    return permissions


def current_user_identifier() -> str:
    return str(st.session_state.get("user_id", ""))


def current_username() -> str:
    return str(st.session_state.get("username", ""))


def history_titles() -> list[str]:
    try:
        records = get_collection().find(
            {
                "$or": [
                    {"user_id": current_user_identifier()},
                    {"username": current_username()},
                    {"owner_identifier": current_username()},
                ],
                "type": "chat",
            },
            {"title": 1},
        ).sort("updated_at", -1).limit(50)
        titles = list(dict.fromkeys(record.get("title", "Untitled") for record in records if record.get("title")))
        st.session_state.pop("mongo_error", None)
        return titles
    except Exception as exc:
        failure = str(exc)
        if st.session_state.get("mongo_error") != failure:
            set_connection_failure(
                exc,
                endpoint="mongodb://Atlas",
                payload={"collection": COLLECTION_NAME, "operation": "find", "type": "chat"},
                title="Chat history unavailable",
                message="Saved conversations could not be loaded. Check the database connection and try again.",
            )
        st.session_state["mongo_error"] = failure
        return []


def save_record(record: dict[str, Any]) -> None:
    owner_identifier = current_user_identifier()
    if not owner_identifier:
        return
    try:
        get_collection().update_one(
            {"user_id": owner_identifier, "chat_id": record.get("chat_id"), "type": "chat"},
            {
                "$set": {
                    **record,
                    "user_id": owner_identifier,
                    "username": current_username(),
                    "owner_identifier": current_username(),
                    "updated_at": datetime.now(timezone.utc),
                }
            },
            upsert=True,
        )
        st.session_state.pop("mongo_error", None)
    except Exception as exc:
        set_connection_failure(
            exc,
            endpoint="mongodb://Atlas",
            payload={"collection": COLLECTION_NAME, "operation": "update_one", "chat_id": record.get("chat_id")},
            title="Chat could not be saved",
            message="Your response is available, but saving it to chat history failed.",
        )


def load_chat(title: str) -> None:
    try:
        record = get_collection().find_one(
            {
                "$or": [
                    {"user_id": current_user_identifier()},
                    {"username": current_username()},
                    {"owner_identifier": current_username()},
                ],
                "title": title,
                "type": "chat",
            },
            sort=[("updated_at", -1)],
        )
        if record:
            st.session_state.messages = record.get("messages", [])
            st.session_state.chat_title = title
            st.session_state.chat_id = record.get("chat_id") or str(uuid.uuid4())
    except Exception as exc:
        set_connection_failure(
            exc,
            endpoint="mongodb://Atlas",
            payload={"collection": COLLECTION_NAME, "operation": "find_one", "chat_title": title},
            title="Chat could not be loaded",
            message="The selected conversation could not be opened.",
        )


def login_user(username: str, password: str) -> dict[str, Any] | None:
    normalized_username = username.strip()
    if not normalized_username:
        return None
    try:
        users = get_users_collection()
        user = users.find_one({
            "$or": [
                {"username": {"$regex": f"^{re.escape(normalized_username)}$", "$options": "i"}},
                {
                    "identifier": {"$regex": f"^{re.escape(normalized_username)}$", "$options": "i"},
                    "username": {"$exists": False},
                },
                {
                    "email": {"$regex": f"^{re.escape(normalized_username)}$", "$options": "i"},
                    "username": {"$exists": False},
                },
            ]
        })
    except Exception as exc:
        set_connection_failure(
            exc,
            endpoint="mongodb://Atlas",
            payload={"collection": "users", "operation": "find_one", "username": normalized_username},
            title="Sign in unavailable",
            message="The account service could not be reached. Please try again.",
        )
        raise
    if not user or not user.get("is_active", True):
        return None

    password_hash = user.get("hashed_password") or user.get("password_hash")
    if not password_hash:
        return None
    try:
        PASSWORD_HASHER.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return None
    verified_username = user.get("username") or user.get("identifier") or user.get("email") or normalized_username
    is_admin = is_admin_account(user)
    return {
        "user_id": str(user.get("_id", "")),
        "username": verified_username,
        "name": user.get("full_name") or user.get("name") or verified_username,
        "role": "admin" if is_admin else "user",
        "is_admin": is_admin,
        "profile_image": user.get("profile_image"),
    }


def authenticate_user(username: str, password: str) -> dict[str, Any] | None:
    return login_user(username, password)


def configured_admin_bootstrap_token() -> str:
    if ADMIN_BOOTSTRAP_TOKEN:
        return ADMIN_BOOTSTRAP_TOKEN
    try:
        return str(st.secrets.get("ADMIN_BOOTSTRAP_TOKEN", "")).strip()
    except Exception:
        return ""


def _find_account_by_username(username: str) -> dict[str, Any] | None:
    normalized_username = validate_username(username)
    return get_users_collection().find_one(
        {"username": {"$regex": f"^{re.escape(normalized_username)}$", "$options": "i"}}
    )


def configure_password_recovery(
    username: str,
    current_password: str,
    security_question: str,
    security_answer: str,
) -> None:
    if security_question not in SECURITY_QUESTIONS:
        raise ValueError("Choose a security question from the list.")
    normalized_answer = normalize_security_answer(security_answer)
    if not normalized_answer or len(normalized_answer) > 200:
        raise ValueError("Enter a security answer with 1–200 characters.")

    account = _find_account_by_username(username)
    if account is None or not account.get("is_active", True):
        raise ValueError("Account recovery settings could not be updated.")
    password_hash = account.get("hashed_password") or account.get("password_hash")
    if not password_hash:
        raise ValueError("The current password is incorrect.")
    try:
        PASSWORD_HASHER.verify(password_hash, current_password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        raise ValueError("The current password is incorrect.") from None

    get_users_collection().update_one(
        {"_id": account["_id"]},
        {
            "$set": {
                "security_question": security_question,
                "security_answer_hash": PASSWORD_HASHER.hash(normalized_answer),
            },
            "$unset": {
                "password_reset_attempts": "",
                "password_reset_locked_until": "",
            },
        },
    )


def normalize_profile_image(image_data: bytes) -> bytes:
    if not image_data:
        raise ValueError("Choose a profile photo to upload.")
    if len(image_data) > MAX_PROFILE_IMAGE_BYTES:
        raise ValueError("Profile photos must be 2 MB or smaller.")

    try:
        with Image.open(io.BytesIO(image_data)) as uploaded_image:
            if uploaded_image.width * uploaded_image.height > 25_000_000:
                raise ValueError("The image dimensions are too large. Choose a smaller photo.")
            image = ImageOps.exif_transpose(uploaded_image).convert("RGB")
            image = ImageOps.fit(image, PROFILE_IMAGE_SIZE, method=Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=86, optimize=True)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("That file could not be read as a valid image.") from exc
    return output.getvalue()


def save_profile_image(username: str, image_data: bytes) -> bytes:
    normalized_image = normalize_profile_image(image_data)
    account = _find_account_by_username(username)
    if account is None:
        raise ValueError("Your account could not be found. Sign in again and retry.")
    result = get_users_collection().update_one(
        {"_id": account["_id"]},
        {"$set": {"profile_image": normalized_image, "profile_image_content_type": "image/jpeg"}},
    )
    if not result.matched_count:
        raise RuntimeError("The profile photo could not be saved to your account.")
    return normalized_image


def promote_account_to_admin(
    username: str,
    current_password: str,
    bootstrap_token: str,
) -> None:
    normalized_username = validate_username(username)
    if normalized_username != validate_username(ADMIN_USERNAME):
        raise ValueError(
            f"Only the configured administrator username '{ADMIN_USERNAME}' can be promoted."
        )
    if not is_admin_bootstrap_authorized(
        normalized_username,
        bootstrap_token,
        configured_admin_bootstrap_token(),
        admin_username=ADMIN_USERNAME,
    ):
        raise ValueError("The administrator setup token is invalid or not configured.")

    account = _find_account_by_username(normalized_username)
    if account is None or not account.get("is_active", True):
        raise ValueError("The configured administrator account could not be found.")
    password_hash = account.get("hashed_password") or account.get("password_hash")
    if not password_hash:
        raise ValueError("The current password is incorrect.")
    try:
        PASSWORD_HASHER.verify(password_hash, current_password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        raise ValueError("The current password is incorrect.") from None

    result = get_users_collection().update_one(
        {"_id": account["_id"], "is_active": {"$ne": False}},
        {"$set": {"role": "admin", "is_admin": True}},
    )
    if not result.matched_count:
        raise ValueError("Administrator access could not be granted to this account.")


def register_user(
    identifier: str,
    username: str,
    full_name: str,
    password: str,
    security_question: str,
    security_answer: str,
    admin_bootstrap_token: str = "",
) -> None:
    normalized_identifier = identifier.strip()
    normalized_username = validate_username(username)
    if len(password) < 8:
        raise ValueError("Password must contain at least 8 characters.")
    if security_question not in SECURITY_QUESTIONS:
        raise ValueError("Choose a security question from the list.")
    normalized_answer = normalize_security_answer(security_answer)
    if not normalized_answer or len(normalized_answer) > 200:
        raise ValueError("Enter a security answer with 1–200 characters.")
    bootstrap_token = configured_admin_bootstrap_token()
    is_admin = normalized_username == validate_username(ADMIN_USERNAME)
    if is_admin:
        if not is_admin_bootstrap_authorized(
            username,
            admin_bootstrap_token,
            bootstrap_token,
            admin_username=ADMIN_USERNAME,
        ):
            raise ValueError(
                "The reserved admin username can only be created with the configured admin setup token."
            )
    elif admin_bootstrap_token:
        raise ValueError("The admin setup token is only valid for the reserved admin username.")
    try:
        users = get_users_collection()
        existing = users.find_one({
            "$or": [
                {"identifier": normalized_identifier},
                {"email": normalized_identifier},
                {"username": {"$regex": f"^{re.escape(normalized_username)}$", "$options": "i"}},
                {"identifier": {"$regex": f"^{re.escape(normalized_username)}$", "$options": "i"}},
                {"email": {"$regex": f"^{re.escape(normalized_username)}$", "$options": "i"}},
            ]
        })
    except Exception as exc:
        set_connection_failure(
            exc,
            endpoint="mongodb://Atlas",
            payload={"collection": "users", "operation": "find_one", "username": normalized_username},
            title="Account creation unavailable",
            message="The account service could not be reached. Please try again.",
        )
        raise
    if existing:
        raise ValueError("That email/mobile number or username is already registered.")

    user = {
        "identifier": normalized_identifier,
        "username": normalized_username,
        "email": normalized_identifier if "@" in normalized_identifier else None,
        "hashed_password": PASSWORD_HASHER.hash(password),
        "security_question": security_question,
        "security_answer_hash": PASSWORD_HASHER.hash(normalized_answer),
        "full_name": full_name.strip(),
        "role": "admin" if is_admin else "user",
        "is_active": True,
        "is_admin": is_admin,
        "created_at": datetime.now(timezone.utc),
    }
    try:
        result = users.insert_one(user)
    except DuplicateKeyError as exc:
        raise ValueError("That username is already registered. Usernames must be unique.") from exc
    except Exception as exc:
        set_connection_failure(
            exc,
            endpoint="mongodb://Atlas",
            payload={"collection": "users", "operation": "insert_one", "username": normalized_username},
            title="Account creation failed",
            message="Your account could not be saved. Please try again.",
        )
        raise
    start_user_session(
        {
            "user_id": str(result.inserted_id),
            "username": normalized_username,
            "name": full_name.strip() or normalized_identifier,
            "role": "admin" if is_admin else "user",
            "is_admin": is_admin,
            "profile_image": None,
        }
    )


def find_password_reset_user(identifier: str) -> dict[str, Any] | None:
    normalized = identifier.strip()
    if not normalized:
        return None
    users = get_users_collection()
    return users.find_one(
        {
            "$or": [
                {"username": {"$regex": f"^{re.escape(normalized)}$", "$options": "i"}},
                {"identifier": {"$regex": f"^{re.escape(normalized)}$", "$options": "i"}},
                {"email": {"$regex": f"^{re.escape(normalized)}$", "$options": "i"}},
            ]
        },
        {
            "username": 1,
            "identifier": 1,
            "security_question": 1,
            "security_answer_hash": 1,
            "password_reset_attempts": 1,
            "password_reset_locked_until": 1,
        },
    )


def reset_password_with_security_answer(
    user_id: Any,
    answer: str,
    new_password: str,
) -> None:
    if len(new_password) < 8:
        raise ValueError("Password must contain at least 8 characters.")
    normalized_answer = normalize_security_answer(answer)
    if not normalized_answer:
        raise ValueError("Enter your security answer.")

    users = get_users_collection()
    now = datetime.now(timezone.utc)
    account = users.find_one({"_id": user_id})
    if account is None:
        raise ValueError("Password reset could not be completed. Check your account details.")
    if not account.get("is_active", True):
        raise ValueError("Password reset is unavailable for inactive accounts.")
    locked = users.find_one(
        {"_id": user_id, "password_reset_locked_until": {"$gt": now}},
        {"password_reset_locked_until": 1},
    )
    if locked:
        raise ValueError(
            f"Too many incorrect answers. Try again in {PASSWORD_RESET_LOCK_MINUTES} minutes."
        )

    answer_hash = account.get("security_answer_hash")
    if not answer_hash:
        raise ValueError(
            "Password reset is unavailable because no security question is saved for this account."
        )
    try:
        PASSWORD_HASHER.verify(answer_hash, normalized_answer)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        updated = users.find_one_and_update(
            {"_id": user_id},
            {"$inc": {"password_reset_attempts": 1}},
            projection={"password_reset_attempts": 1},
            return_document=ReturnDocument.AFTER,
        )
        attempts = int((updated or {}).get("password_reset_attempts", 1))
        if attempts >= PASSWORD_RESET_MAX_ATTEMPTS:
            users.update_one(
                {"_id": user_id},
                {
                    "$set": {
                        "password_reset_locked_until": now
                        + timedelta(minutes=PASSWORD_RESET_LOCK_MINUTES)
                    },
                    "$unset": {"password_reset_attempts": ""},
                },
            )
        raise ValueError("The security answer is incorrect.")

    result = users.update_one(
        {
            "_id": user_id,
            "$or": [
                {"password_reset_locked_until": {"$exists": False}},
                {"password_reset_locked_until": {"$lte": now}},
            ],
        },
        {
            "$set": {"hashed_password": PASSWORD_HASHER.hash(new_password)},
            "$unset": {
                "password_reset_attempts": "",
                "password_reset_locked_until": "",
            },
        },
    )
    if not result.matched_count:
        raise ValueError(
            f"Too many incorrect answers. Try again in {PASSWORD_RESET_LOCK_MINUTES} minutes."
        )
    revoke_user_auth_sessions(user_id)


def _session_user_id(user_id: str) -> ObjectId:
    try:
        return ObjectId(user_id)
    except InvalidId as exc:
        raise ValueError("Your account session is invalid. Sign in again.") from exc


def create_persistent_auth_session(user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    get_auth_sessions_collection().insert_one(
        {
            "_id": hashlib.sha256(token.encode("utf-8")).hexdigest(),
            "user_id": _session_user_id(user_id),
            "created_at": now,
            "expires_at": now + timedelta(days=AUTH_SESSION_TTL_DAYS),
        }
    )
    return token


def revoke_user_auth_sessions(user_id: str | ObjectId) -> None:
    if isinstance(user_id, str):
        user_id = _session_user_id(user_id)
    get_auth_sessions_collection().delete_many({"user_id": user_id})


def revoke_persistent_auth_session(token: str) -> None:
    if token:
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        get_auth_sessions_collection().delete_one({"_id": token_hash})


def user_from_persistent_auth_session(token: str) -> dict[str, Any] | None:
    if not token or len(token) > 256:
        return None
    now = datetime.now(timezone.utc)
    session = get_auth_sessions_collection().find_one(
        {
            "_id": hashlib.sha256(token.encode("utf-8")).hexdigest(),
            "expires_at": {"$gt": now},
        }
    )
    if not session:
        return None
    user = get_users_collection().find_one({"_id": session["user_id"]})
    if not user or not user.get("is_active", True):
        get_auth_sessions_collection().delete_one({"_id": session["_id"]})
        return None
    username = user.get("username") or user.get("identifier") or user.get("email") or ""
    is_admin = is_admin_account(user)
    return {
        "user_id": str(user["_id"]),
        "username": username,
        "name": user.get("full_name") or user.get("name") or username,
        "role": "admin" if is_admin else "user",
        "is_admin": is_admin,
        "profile_image": user.get("profile_image"),
    }


def browser_auth_session_token() -> str:
    return str(st.context.cookies.get(AUTH_SESSION_COOKIE, ""))


def set_browser_auth_cookie(token: str) -> None:
    cookie = json.dumps(f"{AUTH_SESSION_COOKIE}={token}")
    st.html(
        f"""
        <script>
        const authCookie = {cookie};
        const secureAttribute = window.location.protocol === "https:" ? "; Secure" : "";
        document.cookie = authCookie + "; Path=/; Max-Age={AUTH_SESSION_TTL_DAYS * 24 * 60 * 60}; SameSite=Strict" + secureAttribute;
        </script>
        """,
        unsafe_allow_javascript=True,
    )


def clear_browser_auth_cookie() -> None:
    cookie = json.dumps(f"{AUTH_SESSION_COOKIE}=; Path=/; Max-Age=0; SameSite=Strict")
    st.html(
        f"""
        <script>
        const expiredCookie = {cookie};
        const secureAttribute = window.location.protocol === "https:" ? "; Secure" : "";
        document.cookie = expiredCookie + secureAttribute;
        </script>
        """,
        unsafe_allow_javascript=True,
    )


def start_user_session(user: dict[str, Any], *, persist: bool = True) -> None:
    token = create_persistent_auth_session(user["user_id"]) if persist else ""
    st.session_state["user_id"] = user["user_id"]
    st.session_state["username"] = user["username"]
    st.session_state["user"] = {
        "identifier": user["username"],
        "name": user["name"],
        "role": user["role"],
        "is_admin": user["is_admin"],
    }
    st.session_state["is_admin"] = user["is_admin"]
    st.session_state["profile_image"] = user.get("profile_image")
    st.session_state["nav"] = "Admin Dashboard" if user["is_admin"] else "New chat"
    st.session_state.messages = []
    st.session_state.chat_title = "New Chat"
    st.session_state.chat_id = str(uuid.uuid4())
    st.session_state["auth_session_token"] = token
    if token:
        set_browser_auth_cookie(token)


def render_auth_screen() -> None:
    st.markdown(
        '<section class="hero"><p class="eyebrow">✦ PRIVATE AI WORKSPACE</p>'
        '<h1>Welcome to <span>Auralis.</span></h1>'
        '<p>Sign in to access your private chats and saved history.</p></section>',
        unsafe_allow_html=True,
    )
    _, panel, _ = st.columns([1, 1.15, 1])
    with panel:
        login_tab, create_account_tab = st.tabs(["Login", "Create Account"])
        with login_tab:
            with st.form("auralis_login_form", clear_on_submit=False):
                login_username = st.text_input("Username", key="login_username")
                login_password = st.text_input("Password", type="password", key="login_password")
                login_submitted = st.form_submit_button("Login", use_container_width=True)
            with st.expander("Forgot password?"):
                with st.form("auralis_password_reset_lookup_form"):
                    reset_identifier = st.text_input(
                        "Username or email/mobile",
                        key="password_reset_identifier",
                    )
                    reset_lookup_submitted = st.form_submit_button(
                        "Find security question",
                        use_container_width=True,
                    )
                if reset_lookup_submitted:
                    try:
                        reset_account = find_password_reset_user(reset_identifier)
                        if reset_account and reset_account.get("security_question"):
                            st.session_state["password_reset_user_id"] = reset_account["_id"]
                            st.session_state["password_reset_username"] = (
                                reset_account.get("username") or reset_account.get("identifier") or ""
                            )
                            st.session_state["password_reset_question"] = reset_account["security_question"]
                            st.session_state.pop("password_reset_unavailable", None)
                        else:
                            st.session_state.pop("password_reset_user_id", None)
                            st.session_state.pop("password_reset_username", None)
                            st.session_state.pop("password_reset_question", None)
                            st.session_state["password_reset_unavailable"] = True
                        st.rerun()
                    except Exception as exc:
                        set_connection_failure(
                            exc,
                            endpoint="MongoDB Atlas",
                            payload={"collection": "users", "operation": "password_reset_lookup"},
                            title="Password reset unavailable",
                            message="The account recovery service could not be reached. Please try again.",
                        )

                if st.session_state.get("password_reset_question"):
                    st.info(f"Security question: {st.session_state['password_reset_question']}")
                    with st.form("auralis_password_reset_form", clear_on_submit=True):
                        reset_answer = st.text_input("Security answer", type="password")
                        new_password = st.text_input("New password", type="password")
                        confirm_new_password = st.text_input("Confirm new password", type="password")
                        reset_submitted = st.form_submit_button(
                            "Reset password and sign in",
                            use_container_width=True,
                        )
                    if reset_submitted:
                        if new_password != confirm_new_password:
                            set_feedback("error", "Passwords do not match", "Enter the same new password twice.")
                        else:
                            try:
                                reset_password_with_security_answer(
                                    st.session_state["password_reset_user_id"],
                                    reset_answer,
                                    new_password,
                                )
                                reset_user = login_user(
                                    st.session_state["password_reset_username"],
                                    new_password,
                                )
                                if reset_user is None:
                                    raise RuntimeError(
                                        "The password was changed, but the account could not be signed in."
                                    )
                                start_user_session(reset_user)
                                for reset_key in (
                                    "password_reset_user_id",
                                    "password_reset_username",
                                    "password_reset_question",
                                    "password_reset_unavailable",
                                ):
                                    st.session_state.pop(reset_key, None)
                                st.toast(
                                    "Password reset successful. You are now signed in.",
                                    icon="✅",
                                    duration="short",
                                )
                                st.rerun()
                            except ValueError as exc:
                                set_feedback("error", "Password reset failed", str(exc))
                            except Exception as exc:
                                set_connection_failure(
                                    exc,
                                    endpoint="MongoDB Atlas / authentication",
                                    payload={"collection": "users", "operation": "password_reset"},
                                    title="Password reset failed",
                                    message="Your password could not be reset. Please try again.",
                                )
                elif st.session_state.get("password_reset_unavailable"):
                    st.info(
                        "Password recovery is unavailable for that account. Check the details or contact an administrator."
                    )
        with create_account_tab:
            with st.form("auralis_create_account_form", clear_on_submit=False):
                create_identifier = st.text_input("Email or mobile", key="create_identifier")
                create_username = st.text_input(
                    "Username",
                    help="Use 3–32 letters, numbers, periods, underscores, or hyphens.",
                    key="create_username",
                )
                full_name = st.text_input("Name", key="create_full_name")
                create_password = st.text_input("Password", type="password", key="create_password")
                confirm_password = st.text_input("Confirm password", type="password", key="confirm_password")
                security_question = st.selectbox(
                    "Security question",
                    SECURITY_QUESTIONS,
                    key="create_security_question",
                )
                security_answer = st.text_input(
                    "Security answer",
                    type="password",
                    key="create_security_answer",
                )
                admin_bootstrap_token = st.text_input(
                    f"Admin setup token for '{ADMIN_USERNAME}' (leave blank for a regular account)",
                    type="password",
                    help="The configured administrator username requires a token set by the workspace owner.",
                    key="create_admin_bootstrap_token",
                )
                create_submitted = st.form_submit_button("Create Account", use_container_width=True)

        if login_submitted:
            if not login_username.strip() or not login_password:
                set_feedback("error", "Sign-in details required", "Enter your username and password.")
                return
            try:
                user = login_user(login_username, login_password)
                if user is None:
                    details = log_connection_event(
                        "authentication_failed",
                        endpoint="Auralis authentication",
                        payload={"username": login_username.strip(), "reason": "invalid credentials or inactive account"},
                        level=logging.WARNING,
                    )
                    set_feedback(
                        "error",
                        "Unable to sign in",
                        "Check your username and password, then try again.",
                        details=details,
                    )
                    return
                start_user_session(user)
                log_connection_event(
                    "authentication_succeeded",
                    endpoint="Auralis authentication",
                    payload={"username": user["username"], "role": user["role"]},
                )
                st.session_state.pop("feedback_dialog", None)
                st.toast(
                    "Login successful. Welcome to your workspace.",
                    icon="✅",
                    duration="short",
                )
            except Exception as exc:
                if not st.session_state.get("feedback_dialog"):
                    set_connection_failure(
                        exc,
                        endpoint="MongoDB Atlas / authentication",
                        payload={"operation": "login", "username": login_username.strip()},
                        title="Authentication failed",
                        message="The account request could not be completed. See the technical details for more information.",
                    )

        if create_submitted:
            if (
                not create_identifier.strip()
                or not create_username.strip()
                or not full_name.strip()
                or not create_password
                or not security_answer.strip()
            ):
                set_feedback(
                    "error",
                    "Account details required",
                    "Enter your email or mobile number, unique username, name, password, and security answer.",
                )
                return
            if create_password != confirm_password:
                set_feedback("error", "Passwords do not match", "Enter the same password in both fields.")
                return
            try:
                register_user(
                    create_identifier,
                    create_username,
                    full_name,
                    create_password,
                    security_question,
                    security_answer,
                    admin_bootstrap_token,
                )
            except ValueError as exc:
                set_feedback(
                    "error",
                    "Account details need attention",
                    str(exc),
                )
                return
            except Exception as exc:
                if not st.session_state.get("feedback_dialog"):
                    set_connection_failure(
                        exc,
                        endpoint="MongoDB Atlas / authentication",
                        payload={"operation": "create_account", "username": create_username.strip()},
                        title="Account creation failed",
                        message="The account request could not be completed. See the technical details for more information.",
                    )
                return
            log_connection_event(
                "account_created",
                endpoint="MongoDB Atlas / users",
                payload={"operation": "insert_one", "username": create_username.strip()},
            )
            set_feedback(
                "success",
                "Account created",
                "Your account is ready. Continue to your private workspace.",
                transaction={"operation": "account creation", "status": "complete"},
            )


def auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {get_next_hf_token()}"}


def _effective_gemini_credentials() -> tuple[ProviderCredential, ...]:
    configured = get_provider_credentials("Gemini")
    if configured:
        return configured
    try:
        secret_key = str(st.secrets.get("GEMINI_API_KEY", "")).strip()
    except Exception:
        secret_key = ""
    if not secret_key or secret_key.casefold().startswith("replace-with-"):
        secret_key = GEMINI_API_KEY.strip()
    if secret_key in {"YOUR_FALLBACK_KEY_HERE", "PASTE_YOUR_ACTUAL_GEMINI_KEY_HERE"} or secret_key.casefold().startswith("replace-with-"):
        return ()
    return (ProviderCredential(secret_key, "GEMINI_API_KEY"),) if secret_key else ()


def effective_gemini_api_key() -> str:
    credentials = _effective_gemini_credentials()
    return credentials[0].value if credentials else ""


def _is_model_unavailable(error: Exception) -> bool:
    """True when trying another model could succeed.

    Covers both a retired model (404) and a model whose free-tier quota is spent
    (429): Google grants quota per (project, model), so an exhausted model says
    nothing about the others, and skipping to the next one keeps chat working.
    """
    status = getattr(error, "status_code", None) or getattr(error, "code", None)
    message = str(error).lower()
    return status in (404, 429) or any(
        phrase in message
        for phrase in (
            "model not found", "model is not found", "not_found",
            "not supported for generatecontent",
            "resource_exhausted", "quota", "rate limit", "too many requests",
        )
    )


def _available_gemini_models(client: Any, selected_model: str) -> list[str]:
    preferred_family = "pro" if "pro" in selected_model else "flash"
    discovered: list[str] = []
    try:
        for model in client.models.list():
            supported_actions = getattr(model, "supported_actions", None) or []
            model_name = str(getattr(model, "name", "")).removeprefix("models/")
            if model_name.startswith("gemini-") and (
                not supported_actions or any("generatecontent" in action.lower() for action in supported_actions)
            ):
                discovered.append(model_name)
    except Exception:
        pass

    discovered.sort(
        key=lambda name: (
            0 if preferred_family in name.lower() else 1,
            0 if "flash" in name.lower() else 1,
            name,
        )
    )
    # Keep the configured chain (newest-first) ahead of anything discovered, and never
    # fall back to retired ids such as gemini-2.0-flash / gemini-1.5-flash.
    configured = [GEMINI_MODEL, *GEMINI_MODEL_FALLBACKS]
    return list(dict.fromkeys([*configured, *discovered]))


def gemini_generate(
    prompt: str,
    images: list[Any],
    audio_data: bytes | None = None,
    audio_mime_type: str = "audio/wav",
    on_chunk: Callable[[str], None] | None = None,
) -> str:
    credentials = _effective_gemini_credentials()
    if not credentials:
        return "The AI service is not configured. Set `GEMINI_API_KEY` in the environment or Streamlit secrets."

    from google import genai as genai_client
    from google.genai import types

    if audio_data and prompt.strip() == "🎙️ Voice message":
        prompt = "Listen to this voice message and respond naturally in the same language."
    parts: list[Any] = [prompt]
    for image in images:
        image_buffer = io.BytesIO()
        image.save(image_buffer, format="PNG")
        parts.append(types.Part.from_bytes(data=image_buffer.getvalue(), mime_type="image/png"))
    if audio_data:
        parts.append(types.Part.from_bytes(data=audio_data, mime_type=audio_mime_type))

    selected_model = (
        os.getenv("GEMINI_PRO_MODEL", "gemini-2.5-pro").strip()
        if st.session_state.get("model") == "Pro"
        else GEMINI_MODEL
    )

    def generate(key: str) -> Any:
        client = genai_client.Client(api_key=key)

        def generate_stream(model_name: str) -> str:
            chunks: list[str] = []
            try:
                for chunk in client.models.generate_content_stream(
                    model=model_name,
                    contents=parts,
                ):
                    text = getattr(chunk, "text", None)
                    if text:
                        chunks.append(text)
                        if on_chunk is not None:
                            on_chunk(text)
            except Exception as exc:
                if chunks:
                    raise RuntimeError(
                        "The Gemini response stream stopped after partial output."
                    ) from exc
                raise
            return "".join(chunks)

        try:
            if on_chunk is not None:
                try:
                    streamed_response = generate_stream(selected_model)
                    if streamed_response:
                        return streamed_response
                except Exception as first_error:
                    if not _is_model_unavailable(first_error):
                        raise
                    last_error = first_error
                    for fallback_model in _available_gemini_models(client, selected_model):
                        if fallback_model == selected_model:
                            continue
                        try:
                            streamed_response = generate_stream(fallback_model)
                            if streamed_response:
                                return streamed_response
                        except Exception as fallback_error:
                            if not _is_model_unavailable(fallback_error):
                                raise
                            last_error = fallback_error
                    raise RuntimeError(
                        "The configured AI provider key is accepted, but no text-generation model is available. "
                        "Check the provider project/model access and try again. "
                        f"Last model error: {last_error}"
                    ) from last_error
            try:
                return client.models.generate_content(model=selected_model, contents=parts)
            except Exception as first_error:
                if not _is_model_unavailable(first_error):
                    raise
                last_error = first_error
                for fallback_model in _available_gemini_models(client, selected_model):
                    if fallback_model == selected_model:
                        continue
                    try:
                        return client.models.generate_content(model=fallback_model, contents=parts)
                    except Exception as fallback_error:
                        if not _is_model_unavailable(fallback_error):
                            raise
                        last_error = fallback_error
                raise RuntimeError(
                    "The configured AI provider key is accepted, but no text-generation model is available. "
                    "Check the provider project/model access and try again. "
                    f"Last model error: {last_error}"
                ) from last_error
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()

    started_at = time.perf_counter()
    response = call_with_key_rotation(
        "Gemini",
        generate,
        endpoint=f"Gemini/{selected_model}",
        payload={"prompt": prompt, "images": len(images), "audio_bytes": len(audio_data or b"")},
        credentials=credentials,
    )
    log_connection_event(
        "provider_request_succeeded",
        endpoint=f"Gemini/{selected_model}",
        payload={
            "duration_ms": round((time.perf_counter() - started_at) * 1000),
            "images": len(images),
            "audio_bytes": len(audio_data or b""),
        },
    )
    answer = response if isinstance(response, str) else getattr(response, "text", None)
    if not answer:
        raise RuntimeError("The AI provider returned no text for this request.")
    return answer


def openrouter_credentials() -> tuple[ProviderCredential, ...]:
    """Return OpenRouter keys from .env without exposing their values."""
    return get_provider_credentials("OpenRouter")


def openrouter_generate(
    prompt: str,
    *,
    system_prompt: str = "",
    on_chunk: Callable[[str], None] | None = None,
) -> str:
    """Stream chat through OpenRouter's currently available free-model router."""
    credentials = openrouter_credentials()
    if not credentials:
        raise RuntimeError("OpenRouter is not configured. Set OPENROUTER_KEY_1 or OPENROUTER_KEY_2.")

    messages = []
    if system_prompt.strip():
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    endpoint = f"{OPENROUTER_BASE_URL}/chat/completions"
    payload = {"model": OPENROUTER_MODEL, "messages": messages, "stream": True}
    last_error: Exception | None = None

    for credential in credentials:
        chunks: list[str] = []
        try:
            with requests.post(
                endpoint,
                headers={
                    "Authorization": f"Bearer {credential.value}",
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream",
                    "HTTP-Referer": "https://auralis.local",
                    "X-Title": APP_TITLE,
                },
                json=payload,
                timeout=(15, 180),
                stream=True,
            ) as response:
                if response.status_code in {401, 403}:
                    raise RuntimeError("OpenRouter rejected the configured API key.")
                if response.status_code in {408, 429, 500, 502, 503, 504}:
                    raise ProviderRateLimitError(
                        "OpenRouter", credential.key_id, 30, response.status_code,
                        "OpenRouter temporary capacity or rate limit",
                    )
                response.raise_for_status()
                for raw_line in response.iter_lines(decode_unicode=True):
                    if not raw_line:
                        continue
                    line = raw_line.strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        event = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    error = event.get("error") if isinstance(event, dict) else None
                    if error:
                        raise RuntimeError(str(error))
                    for choice in event.get("choices", []) if isinstance(event, dict) else []:
                        piece = str((choice.get("delta") or {}).get("content") or "")
                        if piece:
                            chunks.append(piece)
                            if on_chunk is not None:
                                on_chunk(piece)
            answer = "".join(chunks).strip()
            if not answer:
                raise RuntimeError("OpenRouter returned an empty response.")
            log_connection_event(
                "openrouter_request_succeeded", endpoint=endpoint,
                payload={"model": OPENROUTER_MODEL, "key_id": credential.key_id},
            )
            return answer
        except ProviderRateLimitError as exc:
            last_error = exc
            if chunks:
                raise RuntimeError("OpenRouter stream stopped after partial output.") from exc
            continue
        except Exception as exc:
            log_connection_failure(
                exc, endpoint=endpoint,
                payload={"model": OPENROUTER_MODEL, "key_id": credential.key_id},
                function="openrouter_generate",
            )
            raise
    raise last_error or RuntimeError("All configured OpenRouter keys failed.")


LIVE_QUERY_MARKERS = (
    "latest", "today", "current", "right now", "now", "recent", "news",
    "weather", "temperature", "forecast", "stock price", "share price",
    "exchange rate", "who is the", "what happened",
)


def needs_live_web_search(prompt: str) -> bool:
    lowered = prompt.casefold()
    return any(marker in lowered for marker in LIVE_QUERY_MARKERS)


def live_web_context(query: str, *, limit: int = 5) -> tuple[str, list[dict[str, str]]]:
    """Fetch current search snippets without spending an AI/provider quota."""
    response = requests.post(
        "https://html.duckduckgo.com/html/",
        data={"q": query},
        headers={"User-Agent": "Mozilla/5.0 (compatible; AuralisAI/1.0)"},
        timeout=12,
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    sources: list[dict[str, str]] = []
    for result in soup.select(".result"):
        anchor = result.select_one(".result__a")
        if anchor is None:
            continue
        href = str(anchor.get("href") or "")
        if href.startswith("//duckduckgo.com/l/"):
            href = unquote(parse_qs(urlparse("https:" + href).query).get("uddg", [href])[0])
        if not href.startswith(("http://", "https://")):
            continue
        snippet = result.select_one(".result__snippet")
        sources.append(
            {
                "title": anchor.get_text(" ", strip=True)[:200],
                "url": href,
                "snippet": snippet.get_text(" ", strip=True)[:700] if snippet else "",
            }
        )
        if len(sources) >= limit:
            break
    context = "\n\n".join(
        f"[{index}] {source['title']}\nURL: {source['url']}\n{source['snippet']}"
        for index, source in enumerate(sources, start=1)
    )
    return context, sources


def describe_gemini_error(error: Exception) -> str:
    detail = str(error).strip() or error.__class__.__name__
    api_key = effective_gemini_api_key()
    if api_key:
        detail = detail.replace(api_key, "[redacted]")
    normalized = detail.lower()
    status = getattr(error, "status_code", None) or getattr(error, "code", None)
    status_text = f" (HTTP {status})" if isinstance(status, int) else ""

    if "api_key_invalid" in normalized or "api key not valid" in normalized or "invalid api key" in normalized:
        return "The AI provider rejected the configured key. Verify it in the provider console and update `GEMINI_API_KEY`."
    if "permission_denied" in normalized or "permission denied" in normalized or status == 403:
        return "The AI provider denied access. Check project API access and key restrictions."
    if "resource_exhausted" in normalized or "quota" in normalized or status == 429:
        return "The AI provider quota or rate limit has been reached. Check billing or quota and try again later."
    if "not_found" in normalized or "not found" in normalized or status == 404:
        return "The selected AI model or API endpoint is unavailable. Verify model access for this project."
    if isinstance(error, (ImportError, ModuleNotFoundError)):
        return f"An AI provider SDK dependency is missing: {detail}. Install the requirements and restart Streamlit."
    if isinstance(error, (TimeoutError, ConnectionError)) or "connection" in normalized or "timed out" in normalized:
        return "Could not connect to the AI provider. Check your internet or proxy and try again."
    return f"The AI request failed{status_text}: {detail}"


def is_gemini_quota_error(error: Exception) -> bool:
    status = getattr(error, "status_code", None) or getattr(error, "code", None)
    detail = str(error).lower()
    return status == 429 or any(
        term in detail for term in ("resource_exhausted", "quota", "rate limit", "too many requests")
    )


def generate_hf_image(prompt: str, width: int, height: int) -> bytes:
    payload = {"inputs": prompt, "parameters": {"width": width, "height": height}}
    return _request_hf_binary(HF_IMAGE_API, payload, timeout=180, workflow="image generation")


def describe_hugging_face_error(error: Exception) -> tuple[str, str]:
    detail = str(error).strip() or error.__class__.__name__
    normalized = detail.casefold()
    if "no hugging face credentials configured" in normalized:
        return (
            "Hugging Face token not configured",
            "Add a Hugging Face token as HF_TOKEN_1 or HF_API_TOKEN (or configure HF_TOKENS), "
            "then restart the app and try again.",
        )
    if "configure the hugging face space" in normalized:
        return (
            "Image editing Space not configured",
            "Set HF_STYLE_TRANSFER_SPACE and match HF_STYLE_TRANSFER_API_NAME and "
            "HF_STYLE_TRANSFER_INPUTS to that Space's API, then restart the app.",
        )
    if "401 client error" in normalized or "unauthorized" in normalized:
        return (
            "Hugging Face authentication failed",
            "Check that the configured token is valid and has permission to use this model or Space.",
        )
    if "403 client error" in normalized or "forbidden" in normalized:
        return (
            "Hugging Face access denied",
            "Check model access approval, token permissions, and your Hugging Face account settings.",
        )
    if "404 client error" in normalized or "not found" in normalized:
        return (
            "Hugging Face model unavailable",
            "The configured model or Space endpoint could not be found. Verify its URL and API name.",
        )
    if "429 client error" in normalized or "503 server error" in normalized or "gpu busy" in normalized:
        return (
            "Hugging Face is busy",
            "The model is rate-limited or its GPU is busy. Wait briefly and try again.",
        )
    return (
        "Hugging Face request failed",
        "The image service could not complete this request. Expand “Why did this fail?” for diagnostics.",
    )


def generate_hf_text(prompt: str) -> str:
    payload = {
        "model": HF_TEXT_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 512,
    }
    try:
        def generate(token: str) -> str:
            response = requests.post(
                HF_TEXT_API,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=payload,
                timeout=90,
            )
            response.raise_for_status()
            result = response.json()
            answer = None
            if isinstance(result, dict):
                choices = result.get("choices")
                if isinstance(choices, list) and choices:
                    message = choices[0].get("message") or {}
                    answer = message.get("content") or choices[0].get("text")
                # Older / hf-inference shape, kept as a fallback.
                if answer is None:
                    answer = result.get("generated_text") or result.get("text")
            elif isinstance(result, list) and result and isinstance(result[0], dict):
                answer = result[0].get("generated_text")
            if not isinstance(answer, str) or not answer.strip():
                raise RuntimeError("Hugging Face text model returned no response.")
            return answer.strip()

        started_at = time.perf_counter()
        answer = call_with_key_rotation(
            "Hugging Face",
            generate,
            endpoint=HF_TEXT_API,
            payload=payload,
        )
        log_connection_event(
            "provider_request_succeeded",
            endpoint=HF_TEXT_API,
            payload={"workflow": "text generation", "duration_ms": round((time.perf_counter() - started_at) * 1000)},
        )
        return answer
    except Exception as exc:
        details = log_connection_failure(exc, endpoint=HF_TEXT_API, payload=payload)
        raise RuntimeError(f"{exc}\nDiagnostics: {details}") from exc


def generate_hf_video(prompt: str, width: int, height: int) -> bytes:
    payload = {"inputs": prompt, "parameters": {"width": width, "height": height}}
    return _request_hf_binary(HF_VIDEO_API, payload, timeout=240, workflow="video generation")


def _request_hf_binary(endpoint: str, payload: dict[str, Any], *, timeout: int, workflow: str) -> bytes:
    try:
        def generate(token: str) -> bytes:
            response = requests.post(
                endpoint,
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
                timeout=timeout,
            )
            response.raise_for_status()
            if "application/json" in response.headers.get("content-type", ""):
                raise RuntimeError(
                    f"The Hugging Face model is temporarily unavailable; retry {workflow} in a few seconds."
                )
            if not response.content:
                raise RuntimeError(f"The Hugging Face model returned an empty result for {workflow}.")
            return response.content

        started_at = time.perf_counter()
        result = call_with_key_rotation(
            "Hugging Face",
            generate,
            endpoint=endpoint,
            payload={"workflow": workflow, **payload},
        )
        log_connection_event(
            "provider_request_succeeded",
            endpoint=endpoint,
            payload={"workflow": workflow, "duration_ms": round((time.perf_counter() - started_at) * 1000)},
        )
        return result
    except Exception as exc:
        details = log_connection_failure(exc, endpoint=endpoint, payload=payload)
        raise RuntimeError(f"{exc}\nDiagnostics: {details}") from exc


def _configured_gradio_inputs(raw_inputs: str, *, prompt: str = "", image_path: str = "") -> list[Any]:
    try:
        template = json.loads(raw_inputs)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid Gradio inputs JSON: {exc.msg}") from exc
    if not isinstance(template, list):
        raise ValueError("Gradio inputs configuration must be a JSON array.")

    def replace(value: Any) -> Any:
        if value == "{prompt}":
            return prompt
        if value == "{image}":
            from gradio_client import handle_file

            return handle_file(image_path)
        if isinstance(value, list):
            return [replace(item) for item in value]
        if isinstance(value, dict):
            return {key: replace(item) for key, item in value.items()}
        if isinstance(value, str):
            return value.replace("{prompt}", prompt).replace("{image}", image_path)
        return value

    return [replace(value) for value in template]


def _gradio_media_bytes(result: Any, *, endpoint: str) -> bytes:
    if isinstance(result, (list, tuple)):
        for item in result:
            try:
                return _gradio_media_bytes(item, endpoint=endpoint)
            except (TypeError, ValueError):
                continue
        raise ValueError("Hugging Face Space did not return a media file.")
    if isinstance(result, dict):
        for key in ("video", "image", "path", "url", "data"):
            if key in result:
                return _gradio_media_bytes(result[key], endpoint=endpoint)
        raise ValueError("Hugging Face Space returned an unrecognized media result.")
    if isinstance(result, (bytes, bytearray)):
        return bytes(result)
    if isinstance(result, Path):
        if result.is_file():
            return result.read_bytes()
        raise ValueError("Hugging Face Space returned a missing output file.")
    if not isinstance(result, str):
        result = getattr(result, "path", None) or getattr(result, "url", None)
    if isinstance(result, str):
        path = Path(result)
        if path.is_file():
            return path.read_bytes()
        if result.startswith(("https://", "http://")):
            try:
                response = requests.get(result, timeout=240)
                response.raise_for_status()
                if response.content:
                    return response.content
                raise ValueError("Hugging Face Space returned an empty media download.")
            except Exception as exc:
                log_connection_failure(exc, endpoint=endpoint, payload={"operation": "download_result"})
                raise
    raise ValueError("Hugging Face Space returned an unsupported media result.")


def generate_gradio_media(
    workflow: str,
    space: str,
    api_name: str,
    inputs_json: str,
    *,
    prompt: str = "",
    image_path: str = "",
) -> bytes:
    endpoint = f"https://huggingface.co/spaces/{space}" if space else f"gradio://{workflow}"
    payload = {"prompt": prompt, "image": image_path, "api_name": api_name or None}
    try:
        if not space:
            raise RuntimeError(f"Configure the Hugging Face Space for {workflow} in the environment.")
        from gradio_client import Client

        inputs = _configured_gradio_inputs(inputs_json, prompt=prompt, image_path=image_path)

        def predict(token: str) -> Any:
            client = Client(space, token=token)
            try:
                if api_name:
                    return client.predict(*inputs, api_name=api_name)
                return client.predict(*inputs)
            finally:
                close = getattr(client, "close", None)
                if callable(close):
                    close()

        result = call_with_key_rotation(
            "Hugging Face",
            predict,
            endpoint=endpoint,
            payload=payload,
        )
        return _gradio_media_bytes(result, endpoint=endpoint)
    except Exception as exc:
        details = log_connection_failure(exc, endpoint=endpoint, payload=payload)
        raise RuntimeError(f"{exc}\nDiagnostics: {details}") from exc


def parse_file(uploaded_file) -> tuple[str, Any]:
    name = uploaded_file.name
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    data = uploaded_file.getvalue()
    if ext in {"png", "jpg", "jpeg", "webp"}:
        return "image", Image.open(io.BytesIO(data)).convert("RGB")
    if ext == "pdf":
        from pypdf import PdfReader
        text = "\n\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages).strip()
        return "pdf", text or "No selectable text found in this PDF."
    if ext == "docx":
        from docx import Document

        document = Document(io.BytesIO(data))
        paragraphs = [paragraph.text.strip() for paragraph in document.paragraphs if paragraph.text.strip()]
        tables = [
            [[cell.text.strip() for cell in row.cells] for row in table.rows]
            for table in document.tables
        ]
        return "docx", {"paragraphs": paragraphs, "tables": tables}
    if ext == "xlsx":
        from openpyxl import load_workbook

        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        try:
            sheets = {
                sheet.title: [
                    list(row)
                    for row in sheet.iter_rows(values_only=True)
                ]
                for sheet in workbook.worksheets
            }
        finally:
            workbook.close()
        return "xlsx", sheets
    if ext == "pptx":
        from pptx import Presentation

        presentation = Presentation(io.BytesIO(data))
        slides = []
        for slide in presentation.slides:
            paragraphs = [
                paragraph.text.strip()
                for shape in slide.shapes
                if shape.has_text_frame
                for paragraph in shape.text_frame.paragraphs
                if paragraph.text.strip()
            ]
            tables = [
                [[cell.text.strip() for cell in row.cells] for row in shape.table.rows]
                for shape in slide.shapes
                if shape.has_table
            ]
            slides.append({"paragraphs": paragraphs, "tables": tables})
        return "pptx", slides
    if ext == "csv":
        text = data.decode("utf-8-sig", errors="replace")
        return "csv", list(csv.reader(io.StringIO(text)))
    if ext in {"txt", "py", "js", "html", "css", "json", "md"}:
        return "code", data.decode("utf-8", errors="replace")
    raise ValueError(f"Unsupported file type: .{ext}")


def _document_context(kind: str, content: Any) -> str:
    if kind == "docx":
        sections = list(content["paragraphs"])
        for table_index, rows in enumerate(content["tables"], start=1):
            sections.append(f"Table {table_index}:\n" + "\n".join(" | ".join(row) for row in rows))
        return "\n\n".join(sections)
    if kind == "xlsx":
        return "\n\n".join(
            f"Sheet: {sheet_name}\n" + "\n".join(
                " | ".join("" if value is None else str(value) for value in row)
                for row in rows
            )
            for sheet_name, rows in content.items()
        )
    if kind == "pptx":
        sections = []
        for slide_number, slide in enumerate(content, start=1):
            sections.extend(slide["paragraphs"])
            for table_index, rows in enumerate(slide["tables"], start=1):
                sections.append(
                    f"Slide {slide_number}, table {table_index}:\n"
                    + "\n".join(" | ".join(row) for row in rows)
                )
        return "\n".join(sections)
    if kind == "csv":
        return "\n".join(", ".join(row) for row in content)
    return str(content)


def render_sidebar() -> None:
    with st.sidebar:
        user = st.session_state.user
        profile_image = st.session_state.get("profile_image")
        image_css = ""
        if profile_image:
            image_data = base64.b64encode(profile_image).decode("ascii")
            image_css = f"""
                .st-key-profile_avatar [data-testid="stPopover"] button {{
                    background-image:url("data:image/jpeg;base64,{image_data}") !important;
                    background-size:cover !important;
                    background-position:center !important;
                    color:transparent !important;
                    font-size:0 !important;
                }}
                .st-key-profile_avatar [data-testid="stPopover"] button svg {{
                    display:none !important;
                }}
            """
        st.markdown(
            f"""
            <style>
            [data-testid="stSidebarUserContent"] {{ padding:.5rem .65rem .75rem !important; }}
            .st-key-sidebar_brand {{ margin-bottom:.75rem; }}
            .st-key-profile_avatar {{ width:max-content; margin:0; }}
            .st-key-profile_avatar [data-testid="stPopover"] button {{
                width:42px !important;
                min-width:42px !important;
                height:42px !important;
                min-height:42px !important;
                padding:0 !important;
                border-radius:50% !important;
                border:2px solid rgba(34,211,238,.72) !important;
                background-color:rgba(139,92,246,.22) !important;
                box-shadow:0 0 20px rgba(34,211,238,.2);
            }}
            .st-key-sidebar_profile_block [data-testid="stHorizontalBlock"] {{ align-items:center; }}
            .st-key-sidebar_profile_block [data-testid="stButton"] button {{ min-height:2.25rem; }}
            {image_css}
            </style>
            """,
            unsafe_allow_html=True,
        )
        with st.container(key="sidebar_brand"):
            st.markdown(
                "<strong>✦ NEXUS AI</strong><p>● AURALIS V2.0</p>",
                unsafe_allow_html=True,
            )
        if st.button(
            "＋  New chat",
            key="new_chat_sidebar_button",
            type="primary",
            width="stretch",
            help="Start a fresh conversation",
        ):
            st.session_state.nav = "New chat"
            st.session_state.messages = []
            st.session_state.chat_title = "New Chat"
            st.session_state.chat_id = str(uuid.uuid4())
            st.session_state.last_media = None
            st.session_state.last_context_prompt = ""
            st.rerun()

        is_admin = (
            is_admin_account(user)
            and st.session_state.get("is_admin") is True
        )
        # Files, PDFs, Office documents, images and research all live inside AI chat;
        # these entries are the only remaining workspaces.
        navigation_groups = [
            (
                "WORKSPACE",
                [(":material/forum:", "AI Chat", "New chat")],
            ),
            (
                "VIDEO",
                [
                    (":material/movie:", "Video Generation", "Videos"),
                    (":material/auto_awesome:", "Video Editing", "Video Editing"),
                ],
            ),
            (
                "FILE LIBRARY",
                [(":material/folder_open:", "My Files", "Library")],
            ),
        ]
        if is_admin:
            navigation_groups.append(
                ("ADMINISTRATION", [(":material/shield:", "Admin Dashboard", "Admin Dashboard")])
            )
        navigation_groups.append(
            ("PREFERENCES", [(":material/settings:", "Settings", "Settings")])
        )
        for heading, items in navigation_groups:
            st.caption(heading)
            for icon, label, destination in items:
                if st.button(
                    label,
                    key=f"nav_{destination}",
                    icon=icon,
                    width="stretch",
                    type="primary" if st.session_state.get("nav") == destination else "secondary",
                ):
                    st.session_state.nav = destination
                    st.session_state.last_context_prompt = ""

        st.caption("RECENTS")
        titles = history_titles()
        if titles:
            with st.container(height=190, border=False):
                for index, title in enumerate(titles[:6]):
                    selected = title == st.session_state.get("chat_title") and st.session_state.get("nav") == "New chat"
                    if st.button(
                        title,
                        key=f"history_chat_{index}",
                        icon=":material/chat_bubble_outline:",
                        width="stretch",
                        type="secondary",
                    ):
                        load_chat(title)
                        st.session_state.nav = "New chat"
                        st.session_state.last_context_prompt = ""
                    if selected:
                        st.markdown(
                            "<span class='artifact-meta'>Current conversation</span>",
                            unsafe_allow_html=True,
                        )
            if len(titles) > 6:
                with st.expander("TOPICS & MEMORY", expanded=False):
                    for index, title in enumerate(titles[6:16], start=6):
                        if st.button(
                            title,
                            key=f"history_topic_{index}",
                            icon=":material/bookmark_border:",
                            width="stretch",
                            type="secondary",
                        ):
                            load_chat(title)
                            st.session_state.nav = "New chat"
                            st.session_state.last_context_prompt = ""
        elif st.session_state.get("mongo_error"):
            st.caption("Saved chats are unavailable until the database reconnects.")
        else:
            st.caption("Your recent conversations will appear here.")

        gemini_key_count = len(get_provider_credentials("Gemini"))
        hf_key_count = len(get_provider_credentials("Hugging Face"))
        st.markdown(
            "<div class='artifact-meta'>"
            f"● Gemini · {gemini_key_count} key(s) &nbsp; "
            f"● HF video · {hf_key_count} token(s)"
            "</div>",
            unsafe_allow_html=True,
        )
        with st.container(key="sidebar_profile_block", border=True):
            profile_columns = st.columns([0.25, 0.75], vertical_alignment="center")
            with profile_columns[0]:
                with st.container(key="profile_avatar"):
                    with st.popover(
                        " ",
                        icon=":material/account_circle:",
                        width="content",
                        key="profile_menu",
                        help="Profile photo and account actions",
                    ):
                        uploaded_photo = st.file_uploader(
                            "Upload profile photo",
                            type=["png", "jpg", "jpeg", "webp"],
                            key=f"profile_photo_upload_{st.session_state.profile_upload_nonce}",
                            help="JPG, PNG, or WebP, up to 2 MB.",
                        )
                        if uploaded_photo is not None:
                            try:
                                saved_image = save_profile_image(
                                    current_username(),
                                    uploaded_photo.getvalue(),
                                )
                                st.session_state["profile_image"] = saved_image
                                st.session_state.profile_upload_nonce += 1
                                st.toast("Profile photo updated.", icon="✅", duration="short")
                                st.rerun()
                            except ValueError as exc:
                                set_feedback("error", "Profile photo not updated", str(exc))
                            except Exception as exc:
                                set_connection_failure(
                                    exc,
                                    endpoint="MongoDB Atlas / users",
                                    payload={"collection": "users", "operation": "update_profile_image"},
                                    title="Profile photo could not be saved",
                                    message="Your profile photo could not be saved. Please try again.",
                                )
            with profile_columns[1]:
                st.markdown(
                    f"**{escape(current_username())}**  \n"
                    f"<span class='artifact-meta'>NEXUS AI · {'Admin' if is_admin else 'Workspace'}</span>",
                    unsafe_allow_html=True,
                )
            if st.button(
                "Sign out",
                key="sign_out",
                icon=":material/logout:",
                width="stretch",
                type="secondary",
            ):
                auth_token = st.session_state.get("auth_session_token") or browser_auth_session_token()
                try:
                    revoke_persistent_auth_session(auth_token)
                except Exception as exc:
                    log_connection_failure(
                        exc,
                        endpoint="MongoDB Atlas",
                        payload={"collection": "user_sessions", "operation": "logout"},
                    )
                clear_browser_auth_cookie()
                st.session_state.file_input_nonce += 1
                st.session_state.audio_input_nonce += 1
                st.session_state.advanced_video_upload_nonce += 1
                st.session_state.advanced_audio_upload_nonce += 1
                st.session_state.advanced_ai_upload_nonce += 1
                st.session_state.pro_media_nonce += 1
                st.session_state.profile_upload_nonce += 1
                for key in (
                    "user",
                    "user_id",
                    "username",
                    "messages",
                    "chat_title",
                    "chat_id",
                    "auth_notice",
                    "media_result",
                    "advanced_video_result",
                    "mongo_error",
                    "feedback_dialog",
                    "is_admin",
                    "profile_image",
                    "auth_session_token",
                ):
                    st.session_state.pop(key, None)
                st.rerun()


def render_role_settings() -> None:
    user = st.session_state.get("user", {})
    verified_admin = (
        is_admin_account(user)
        and st.session_state.get("is_admin") is True
    )
    requested_admin_panel = st.session_state.get("nav") in {"Admin Panel", "Admin Dashboard"}
    if requested_admin_panel and not verified_admin:
        st.session_state["nav"] = "Settings"
        set_feedback(
            "error",
            "Administrator access required",
            "This account is not authorized to open the administrator panel.",
        )

    st.markdown("## Administrator settings" if verified_admin else "## Account settings")
    st.caption(
        "Administrator permissions were verified from your account record."
        if verified_admin
        else "Manage your personal workspace preferences."
    )
    profile, preferences = st.columns(2, gap="large")
    with profile:
        with st.container(border=True):
            st.markdown("### Account")
            st.text_input("Username", value=str(user.get("username") or st.session_state.get("username", "")), disabled=True)
            st.text_input("Name", value=str(user.get("name", "")), disabled=True)
            st.text_input("Access level", value="Administrator" if verified_admin else "User", disabled=True)
    with preferences:
        with st.container(border=True):
            st.markdown("### Workspace preferences")
            st.session_state.model = st.selectbox(
                "Preferred AI model",
                ["Flash", "Pro"],
                index=0 if st.session_state.get("model") == "Flash" else 1,
                key="chat_model",
            )
            st.caption("The model preference applies to your chat workspace.")

    st.markdown("### Account security")
    with st.expander("Set up or update password recovery"):
        st.caption(
            "Verify your current password and save a security answer. The answer is stored as a one-way hash."
        )
        with st.form("account_recovery_setup_form", clear_on_submit=True):
            recovery_current_password = st.text_input(
                "Current password",
                type="password",
                key="recovery_current_password",
            )
            recovery_question = st.selectbox(
                "Security question",
                SECURITY_QUESTIONS,
                key="recovery_security_question",
            )
            recovery_answer = st.text_input(
                "Security answer",
                type="password",
                key="recovery_security_answer",
            )
            recovery_submitted = st.form_submit_button(
                "Save recovery settings",
                use_container_width=True,
            )
        if recovery_submitted:
            try:
                configure_password_recovery(
                    current_username(),
                    recovery_current_password,
                    recovery_question,
                    recovery_answer,
                )
                st.toast("Password recovery settings saved.", icon="✅", duration="short")
            except ValueError as exc:
                set_feedback("error", "Recovery settings not saved", str(exc))
            except Exception as exc:
                set_connection_failure(
                    exc,
                    endpoint="MongoDB Atlas / authentication",
                    payload={"collection": "users", "operation": "configure_password_recovery"},
                    title="Recovery settings unavailable",
                    message="Your password recovery settings could not be saved.",
                )

    if not verified_admin and current_username().casefold() == ADMIN_USERNAME:
        st.markdown("### Administrator setup")
        st.caption(
            f"This existing account matches the configured administrator username '{ADMIN_USERNAME}'. "
            "Verify your current password and the private setup token to enable administrator access."
        )
        with st.form("existing_admin_setup_form", clear_on_submit=True):
            admin_current_password = st.text_input(
                "Current password",
                type="password",
                key="existing_admin_current_password",
            )
            admin_setup_token = st.text_input(
                "Administrator setup token",
                type="password",
                key="existing_admin_setup_token",
            )
            admin_setup_submitted = st.form_submit_button(
                "Verify and enable admin access",
                use_container_width=True,
            )
        if admin_setup_submitted:
            try:
                promote_account_to_admin(
                    current_username(),
                    admin_current_password,
                    admin_setup_token,
                )
                user["role"] = "admin"
                user["is_admin"] = True
                st.session_state["user"] = user
                st.session_state["is_admin"] = True
                st.session_state["nav"] = "Admin Dashboard"
                st.toast("Administrator access verified.", icon="🛡️", duration="short")
                st.rerun()
            except ValueError as exc:
                set_feedback("error", "Administrator setup failed", str(exc))
            except Exception as exc:
                set_connection_failure(
                    exc,
                    endpoint="MongoDB Atlas / authentication",
                    payload={"collection": "users", "operation": "promote_admin"},
                    title="Administrator setup unavailable",
                    message="Administrator access could not be updated.",
                )

    if not verified_admin:
        return

    st.markdown("### Administrator panel")
    st.caption(
        "Admin access is granted by the account's MongoDB `is_admin` flag or `role: admin`. "
        "Typing an administrator username does not grant administrator privileges."
    )
    try:
        mongo_configured = bool(MONGODB_URL.strip() or str(st.secrets.get("MONGODB_URL", "")).strip())
    except Exception:
        mongo_configured = bool(MONGODB_URL.strip())
    statuses = st.columns(3)
    statuses[0].metric("MongoDB", "Configured" if mongo_configured else "Not configured")
    statuses[1].metric("Gemini", "Configured" if effective_gemini_api_key() else "Not configured")
    statuses[2].metric("Video-to-video Space", "Configured" if HF_VIDEO_TO_VIDEO_SPACE else "Not configured")
    st.info(
        "To grant or revoke administrator access, update the user's `is_admin` or `role` field in the "
        "trusted database. New accounts are always created with user access."
    )


def _admin_inspector_value(value: Any, key: str = "") -> Any:
    if re.search(r"password|secret|token|credential|api.?key", key, re.IGNORECASE):
        return "[redacted]"
    if isinstance(value, dict):
        return {
            str(child_key): _admin_inspector_value(child_value, str(child_key))
            for child_key, child_value in value.items()
            if not str(child_key).startswith("_")
        }
    if isinstance(value, (list, tuple)):
        return [_admin_inspector_value(item) for item in value[:100]]
    if isinstance(value, (bytes, bytearray)):
        return f"[binary data: {len(value)} bytes]"
    if isinstance(value, (str, int, float, bool)) or value is None:
        if isinstance(value, str) and len(value) > 10_000:
            return value[:10_000] + "… [truncated]"
        return value
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return isoformat()
    return str(value)


def render_admin_dashboard() -> None:
    user = st.session_state.get("user", {})
    verified_admin = is_admin_account(user) and st.session_state.get("is_admin") is True
    if not verified_admin:
        st.session_state["nav"] = "Settings"
        set_feedback(
            "error",
            "Administrator access required",
            "This account is not authorized to inspect registered users.",
        )
        return

    st.markdown("## 🛡️ Admin Dashboard")
    st.caption("Read-only account and saved-workspace inspection. Password hashes and credentials are excluded.")
    try:
        user_documents = list(
            get_users_collection()
            .find(
                {},
                {
                    "username": 1,
                    "email": 1,
                    "identifier": 1,
                    "full_name": 1,
                    "name": 1,
                    "role": 1,
                    "is_admin": 1,
                    "is_active": 1,
                    "created_at": 1,
                },
            )
            .sort("created_at", -1)
        )
    except Exception as exc:
        set_connection_failure(
            exc,
            endpoint="mongodb://Atlas",
            payload={"collection": "users", "operation": "admin_list_users"},
            title="User directory unavailable",
            message="Registered users could not be loaded from the database.",
        )
        return

    if not user_documents:
        st.info("No registered users were found.")
        return

    table_rows: list[dict[str, Any]] = []
    for account in user_documents:
        table_rows.append(
            {
                "User ID": str(account.get("_id", "")),
                "Username": account.get("username") or account.get("identifier") or "",
                "Name": account.get("full_name") or account.get("name") or "",
                "Email": account.get("email") or "",
                "Role": "Admin" if is_admin_account(account) else "User",
                "Active": bool(account.get("is_active", True)),
                "Created": _admin_inspector_value(account.get("created_at")),
            }
        )
    st.markdown("### Registered users")
    selection = st.dataframe(
        table_rows,
        hide_index=True,
        use_container_width=True,
        on_select="rerun",
        selection_mode="single-row",
        key="admin_user_directory",
    )
    selected_rows = selection.selection.rows
    if not selected_rows:
        st.caption("Select a user row to inspect that account’s saved chats and generations.")
        return

    selected_index = selected_rows[0]
    if selected_index < 0 or selected_index >= len(user_documents):
        return
    selected_user = user_documents[selected_index]
    selected_id = str(selected_user.get("_id", ""))
    selected_username = str(selected_user.get("username") or "")
    if st.session_state.get("admin_inspector_user_id") != selected_id:
        st.session_state["admin_inspector_user_id"] = selected_id
        st.session_state["admin_inspector_page"] = 0
    st.markdown(
        f"### Saved data for {escape(selected_username or selected_id)}"
    )
    owner_values = {
        value
        for value in (
            selected_id,
            selected_username,
            str(selected_user.get("identifier") or ""),
            str(selected_user.get("email") or ""),
        )
        if value
    }
    owner_query = [
        {"user_id": value} for value in owner_values
    ] + [
        {"username": value} for value in owner_values
    ] + [
        {"owner_identifier": value} for value in owner_values
    ]
    try:
        generations = get_collection()
        records_query = {"$or": owner_query}
        record_count = generations.count_documents(records_query)
    except Exception as exc:
        set_connection_failure(
            exc,
            endpoint="mongodb://Atlas",
            payload={
                "collection": COLLECTION_NAME,
                "operation": "admin_inspect_user_records",
                "user_id": selected_id,
            },
            title="Saved data unavailable",
            message="The selected user’s saved history and generations could not be loaded.",
        )
        return
    if not record_count:
        st.info("This user has no saved chats or generations.")
        return
    page_size = 50
    page_count = (record_count + page_size - 1) // page_size
    current_page = min(
        max(int(st.session_state.get("admin_inspector_page", 0)), 0),
        page_count - 1,
    )
    st.session_state["admin_inspector_page"] = current_page
    previous_column, status_column, next_column = st.columns([1, 3, 1])
    with previous_column:
        if st.button(
            "← Previous",
            disabled=current_page == 0,
            key="admin_inspector_previous",
            use_container_width=True,
        ):
            st.session_state["admin_inspector_page"] -= 1
            st.rerun()
    with status_column:
        st.caption(
            f"Showing records {current_page * page_size + 1}–"
            f"{min((current_page + 1) * page_size, record_count)} of {record_count}."
        )
    with next_column:
        if st.button(
            "Next →",
            disabled=current_page + 1 >= page_count,
            key="admin_inspector_next",
            use_container_width=True,
        ):
            st.session_state["admin_inspector_page"] += 1
            st.rerun()
    records = list(
        generations.find(records_query)
        .sort("updated_at", -1)
        .skip(current_page * page_size)
        .limit(page_size)
    )
    for index, record in enumerate(records):
        record_type = str(record.get("type") or "generation").replace("_", " ").title()
        title = escape(str(record.get("title") or record.get("prompt") or record_type))
        timestamp = _admin_inspector_value(record.get("updated_at") or record.get("created_at"))
        with st.expander(f"{record_type} · {title[:100]} · {timestamp or 'Date unavailable'}"):
            st.json(_admin_inspector_value(record), expanded=True)

    render_admin_user_conversations(owner_values, selected_username or selected_id)


def render_admin_user_conversations(owner_values: set[str], label: str) -> None:
    """Admin-only transcript browser: every chat a user has, including deleted ones."""
    st.markdown("### 💬 Conversations")
    st.caption(
        "Full chat transcripts for this user. Deleting a chat in the app hides it from the user, "
        "but the transcript stays here for the administrator."
    )
    owner_query = [{"user_id": value} for value in owner_values] + [
        {"username": value} for value in owner_values
    ]
    try:
        conversations = list(
            get_conversations_collection()
            .find({"$or": owner_query})
            .sort("updated_at", -1)
            .limit(200)
        )
    except Exception as exc:
        set_connection_failure(
            exc,
            endpoint="mongodb://Atlas",
            payload={"collection": "conversations", "operation": "admin_list_conversations"},
            title="Conversations unavailable",
            message="Chat history for the selected user could not be loaded.",
        )
        return
    if not conversations:
        st.info(f"{label} has no stored conversations yet.")
        return
    total_messages = 0
    rows = []
    for index, conversation in enumerate(conversations, start=1):
        title = str(conversation.get("title") or "Untitled chat")
        chat_id = str(conversation.get("conversation_id") or "")
        updated = _admin_inspector_value(conversation.get("updated_at") or conversation.get("created_at")) or ""
        messages = list(
            get_conversation_messages_collection()
            .find(
                {
                    "user_id": conversation.get("user_id"),
                    "conversation_id": conversation.get("conversation_id"),
                }
            )
            .sort("created_at", 1)
            .limit(400)
        )
        total_messages += len(messages)
        rows.append(
            {
                "#": index,
                "Chat": title[:70],
                "Messages": len(messages),
                "Last activity": updated,
                "Status": "Deleted by user" if conversation.get("deleted_by_user") else "Active",
                "chat_id": chat_id,
            }
        )
    st.dataframe(
        [{key: value for key, value in row.items() if key != "chat_id"} for row in rows],
        hide_index=True,
        use_container_width=True,
    )
    st.caption(f"{len(rows)} conversation(s) · {total_messages} messages")
    labels = {row["chat_id"]: f"{row['#']}. {row['Chat']}" for row in rows}
    selected_chat = st.selectbox(
        "Open conversation",
        list(labels),
        format_func=lambda chat_id: labels[chat_id],
        key=f"admin_conversation_{label}",
    )
    conversation = next((item for item in conversations if str(item.get("conversation_id")) == selected_chat), None)
    if not conversation:
        return
    transcript = list(
        get_conversation_messages_collection()
        .find(
            {
                "user_id": conversation.get("user_id"),
                "conversation_id": conversation.get("conversation_id"),
            }
        )
        .sort("created_at", 1)
        .limit(400)
    )
    with st.container(border=True):
        for message in transcript:
            role = str(message.get("role", "user"))
            author = "🤖 Assistant" if role == "assistant" else "👤 User"
            st.markdown(f"**{author}** · {_admin_inspector_value(message.get('created_at')) or ''}")
            st.markdown(str(message.get("content", "")))
            files = message.get("files") or []
            if files:
                st.caption("Attachments: " + ", ".join(map(str, files)))
            st.divider()
    export_text = "\n\n".join(
        f"[{message.get('role', 'user').upper()}] {message.get('content', '')}" for message in transcript
    )
    st.download_button(
        "Download transcript (.txt)",
        data=export_text or "(empty conversation)",
        file_name=f"{label}_{selected_chat[:8]}_transcript.txt",
        mime="text/plain",
        use_container_width=True,
        key=f"admin_transcript_download_{label}",
    )


def _room_invite_link(invite_code: str) -> str:
    try:
        headers = st.context.headers
        host = str(headers.get("Host", "")).strip()
        scheme = str(headers.get("X-Forwarded-Proto", "https")).split(",", 1)[0].strip().lower()
    except (AttributeError, RuntimeError):
        host, scheme = "", "https"
    if host and scheme in {"http", "https"} and re.fullmatch(r"[A-Za-z0-9.-]+(?::\d{1,5})?", host):
        return f"{scheme}://{host}/?room={invite_code}"
    return f"?room={invite_code}"


def render_chat_rooms() -> None:
    user_id = current_user_identifier()
    username = current_username()
    st.markdown("## 👥 Chat Rooms")
    st.caption("Create a room, share its invite code, and chat with its members.")

    with st.expander("Create a room", expanded=False):
        with st.form("create_chat_room_form"):
            room_name = st.text_input("Room name", max_chars=80)
            create_submitted = st.form_submit_button("Create room", use_container_width=True)
        if create_submitted:
            try:
                room = create_room(
                    get_rooms_collection(),
                    name=room_name,
                    owner_id=user_id,
                    owner_username=username,
                )
                st.session_state["created_room_invite"] = {
                    "name": room["name"],
                    "code": room["invite_code"],
                    "link": _room_invite_link(room["invite_code"]),
                }
                st.rerun()
            except ValueError as exc:
                set_feedback("error", "Room name required", str(exc))
            except Exception as exc:
                set_connection_failure(
                    exc,
                    endpoint="mongodb://Atlas",
                    payload={"collection": "rooms", "operation": "create_room"},
                    title="Room could not be created",
                    message="The room was not saved. Check the database connection and try again.",
                )

    invite = st.session_state.get("created_room_invite")
    if invite:
        with st.container(border=True):
            st.markdown(f"### Invite members to {escape(invite['name'])}")
            st.caption("Share this invite code or link with the people you want to join.")
            st.code(invite["code"], language=None)
            st.code(invite["link"], language=None)
            if st.button("Dismiss invite details", key="dismiss_room_invite"):
                st.session_state.pop("created_room_invite", None)
                st.rerun()

    query_invite = str(st.query_params.get("room", ""))
    with st.form("join_chat_room_form"):
        invite_input = st.text_input(
            "Invite code or link",
            value=query_invite,
            key="chat_room_invite_input",
        )
        join_submitted = st.form_submit_button("Accept & Join", use_container_width=True)
    if join_submitted:
        try:
            join_room(
                get_rooms_collection(),
                invite=invite_input,
                user_id=user_id,
                username=username,
            )
            st.query_params.pop("room", None)
            set_feedback(
                "success",
                "You joined the room",
                "The room is now available in your member list.",
                transaction={"operation": "join room", "status": "complete"},
            )
            st.rerun()
        except (ValueError, LookupError) as exc:
            set_feedback("error", "Could not join room", str(exc))
        except Exception as exc:
            set_connection_failure(
                exc,
                endpoint="mongodb://Atlas",
                payload={"collection": "rooms", "operation": "join_room"},
                title="Could not join room",
                message="The room membership could not be saved. Check the database connection and try again.",
            )

    try:
        rooms = list_member_rooms(get_rooms_collection(), user_id=user_id)
    except Exception as exc:
        set_connection_failure(
            exc,
            endpoint="mongodb://Atlas",
            payload={"collection": "rooms", "operation": "list_member_rooms", "user_id": user_id},
            title="Rooms unavailable",
            message="Your rooms could not be loaded from the database.",
        )
        return
    if not rooms:
        st.info("You have not joined any rooms yet.")
        return

    room_labels = {
        room["room_id"]: f"{room['name']} · {'Owner' if room['owner_id'] == user_id else 'Member'}"
        for room in rooms
    }
    room_ids = list(room_labels)
    selected_room_id = st.selectbox(
        "Your rooms",
        room_ids,
        format_func=lambda room_id: room_labels[room_id],
        key="chat_rooms_selection",
    )
    try:
        selected_room = get_member_room(
            get_rooms_collection(),
            room_id=selected_room_id,
            user_id=user_id,
        )
    except PermissionError as exc:
        set_feedback("error", "Room access unavailable", str(exc))
        return
    except Exception as exc:
        set_connection_failure(
            exc,
            endpoint="mongodb://Atlas",
            payload={"collection": "rooms", "operation": "load_room", "room_id": selected_room_id},
            title="Room unavailable",
            message="This room could not be opened or your membership is no longer active.",
        )
        return

    st.markdown(f"### {escape(str(selected_room.get('name', 'Chat room')))}")
    members = selected_room.get("members", [])
    st.caption(f"{len(members)} member(s)")
    if selected_room.get("owner_id") == user_id:
        st.caption("Invite code")
        st.code(str(selected_room.get("invite_code", "")), language=None)
        st.caption("Invite link")
        st.code(_room_invite_link(str(selected_room.get("invite_code", ""))), language=None)
        non_owner_members = [member for member in members if member.get("user_id") != user_id]
        with st.expander("Manage room members", expanded=False):
            if not non_owner_members:
                st.caption("No other members have joined yet.")
            else:
                for member in members:
                    suffix = " · Owner" if member.get("user_id") == user_id else ""
                    st.write(f"• {member.get('username', 'User')}{suffix}")
                member_options = {
                    str(member["user_id"]): str(member.get("username") or member["user_id"])
                    for member in non_owner_members
                }
                with st.form(f"remove_room_member_form_{selected_room_id}"):
                    member_to_remove = st.selectbox(
                        "Member to remove",
                        list(member_options),
                        format_func=lambda member_id: member_options[member_id],
                    )
                    remove_submitted = st.form_submit_button("Remove member")
                if remove_submitted:
                    try:
                        remove_room_member(
                            get_rooms_collection(),
                            room_id=selected_room_id,
                            owner_id=user_id,
                            member_id=member_to_remove,
                        )
                        st.rerun()
                    except (ValueError, PermissionError) as exc:
                        set_feedback("error", "Member could not be removed", str(exc))
                    except Exception as exc:
                        set_connection_failure(
                            exc,
                            endpoint="mongodb://Atlas",
                            payload={
                                "collection": "rooms",
                                "operation": "remove_member",
                                "room_id": selected_room_id,
                            },
                            title="Member could not be removed",
                            message="Room membership could not be updated.",
                        )

    st.caption("Showing the most recent 200 room messages.")
    for message in selected_room.get("messages", []):
        with st.chat_message("user" if message.get("user_id") == user_id else "assistant"):
            st.caption(
                f"{message.get('username', 'User')} · "
                f"{_admin_inspector_value(message.get('created_at')) or ''}"
            )
            st.write(message.get("content", ""))
    with st.form(f"send_room_message_form_{selected_room_id}"):
        room_message = st.text_area("Message", max_chars=4000, key=f"room_message_{selected_room_id}")
        send_submitted = st.form_submit_button("Send message", use_container_width=True)
    if send_submitted:
        try:
            post_room_message(
                get_rooms_collection(),
                room_id=selected_room_id,
                user_id=user_id,
                username=username,
                content=room_message,
            )
            st.rerun()
        except ValueError as exc:
            set_feedback("error", "Message not sent", str(exc))
        except PermissionError as exc:
            set_feedback("error", "Message not sent", str(exc))
        except Exception as exc:
            set_connection_failure(
                exc,
                endpoint="mongodb://Atlas",
                payload={"collection": "rooms", "operation": "post_message", "room_id": selected_room_id},
                title="Message not sent",
                message="The room message could not be saved.",
            )


@st.dialog("Full Screen View", width="large")
def fullscreen_preview_dialog() -> None:
    preview = st.session_state.get("fullscreen_media")
    if preview:
        if preview["type"] == "video":
            st.video(preview["data"])
        else:
            st.image(preview["data"], use_container_width=True)
    if st.button("❌ Close", key="close_fullscreen_preview", use_container_width=True):
        st.session_state.pop("fullscreen_media", None)
        st.rerun()


def render_media_result() -> None:
    result = st.session_state.get("media_result")
    with st.container(key="nexus-live-preview", border=True):
        st.markdown("### Live Output Preview")
        if result:
            data = result["data"]
            media_type = result["type"]
            if media_type == "image":
                st.image(data, use_container_width=True)
            elif media_type == "video":
                st.video(data)
            if st.button("🔍 Full Screen View", key="open_media_fullscreen", use_container_width=True):
                st.session_state["fullscreen_media"] = {"data": data, "type": media_type}
                st.rerun()
            st.download_button(
                "Download result",
                data=data,
                file_name=result["filename"],
                mime="video/mp4" if media_type == "video" else "image/png",
                use_container_width=True,
            )
        else:
            st.caption("Your generated image or video will appear here.")


def render_fullscreen_preview_if_open() -> None:
    if st.session_state.get("fullscreen_media") and not st.session_state.get("feedback_dialog"):
        fullscreen_preview_dialog()


def render_image_generation(*, render_result: bool = True) -> None:
    st.markdown("## Image tools")
    st.caption("Generate a new image or upload one to edit it. Both workflows are in this section.")
    uploaded_image = st.file_uploader(
        "Upload an image to edit (optional)",
        type=["png", "jpg", "jpeg", "webp"],
        key="style_transfer_upload",
        help="Leave this empty to generate a new image. PNG, JPG, or WebP.",
    )
    if uploaded_image is not None:
        st.image(
            uploaded_image.getvalue(),
            caption="Image to edit",
            width=300,
            alt="Uploaded image selected for editing",
        )
    with st.form("image_generation_form"):
        prompt = st.text_area(
            "Describe the image or the changes you want",
            max_chars=2000,
            placeholder=(
                "Describe a new image, or explain how to transform the uploaded image."
            ),
        )
        ratio_label = st.selectbox(
            "Aspect ratio (used when generating)",
            list(ASPECT_RATIOS),
            disabled=uploaded_image is not None,
        )
        action_label = "Edit uploaded image" if uploaded_image is not None else "Generate image"
        submitted = st.form_submit_button(action_label, use_container_width=True)
    if submitted:
        if not prompt.strip():
            set_feedback(
                "error",
                "A prompt is required",
                "Describe the image you want to generate or the changes you want to make.",
            )
        elif uploaded_image is None:
            width, height = ASPECT_RATIOS[ratio_label]
            try:
                set_active_theme("Images")
                render_active_theme()
                with processing_pulse(), st.spinner("Generating image…"):
                    image = generate_hf_image(prompt.strip(), width, height)
                st.session_state["last_image_prompt"] = prompt.strip()
                st.session_state["media_result"] = {
                    "type": "image",
                    "data": image,
                    "filename": "auralis-generated-image.png",
                }
                log_connection_event(
                    "image_generation_succeeded",
                    endpoint=HF_IMAGE_API,
                    payload={"inputs": prompt, "width": width, "height": height},
                )
                set_feedback(
                    "success",
                    "Image generated",
                    "Your image is ready to preview and download.",
                    transaction={"operation": "image generation", "dimensions": f"{width} × {height}"},
                )
            except Exception as exc:
                error_title, error_message = describe_hugging_face_error(exc)
                set_feedback(
                    "error",
                    error_title,
                    error_message,
                    details=exc,
                )
        else:
            try:
                with tempfile.TemporaryDirectory(prefix="auralis-style-") as directory:
                    image_path = Path(directory) / Path(uploaded_image.name).name
                    image_path.write_bytes(uploaded_image.getvalue())
                    set_active_theme("Images")
                    render_active_theme()
                    with processing_pulse(), st.spinner("Editing uploaded image…"):
                        image = generate_gradio_media(
                            "style transfer",
                            HF_STYLE_TRANSFER_SPACE,
                            HF_STYLE_TRANSFER_API_NAME,
                            HF_STYLE_TRANSFER_INPUTS,
                            prompt=prompt.strip(),
                            image_path=str(image_path),
                        )
                st.session_state["last_image_prompt"] = prompt.strip()
                st.session_state["media_result"] = {
                    "type": "image",
                    "data": image,
                    "filename": "auralis-style-transfer.png",
                }
                set_feedback(
                    "success",
                    "Image edit complete",
                    "Your transformed image is ready to preview and download.",
                    transaction={"operation": "image edit", "space": HF_STYLE_TRANSFER_SPACE},
                )
            except Exception as exc:
                error_title, error_message = describe_hugging_face_error(exc)
                set_feedback(
                    "error",
                    error_title,
                    error_message,
                    details=exc,
                )
    if render_result:
        render_media_result()


def render_video_generation(*, render_result: bool = True) -> None:
    st.markdown("## Video Studio")
    prompt_tab, image_tab, long_tab = st.tabs(
        ["Prompt to video", "Image to video", "30s AI video generator"]
    )

    with prompt_tab:
        st.caption("Describe a scene and generate a video from your prompt.")
        with st.form("nexus_prompt_to_video_form"):
            prompt = st.text_area(
                "Describe your video",
                max_chars=4000,
                placeholder="A cinematic journey through a bioluminescent forest at dawn…",
                key="nexus_prompt_to_video_prompt",
            )
            submitted = st.form_submit_button("Generate video", width="stretch")
        if submitted:
            if not prompt.strip():
                set_feedback("error", "A prompt is required", "Describe the video you want to generate.")
            else:
                try:
                    with processing_pulse(), st.spinner("Generating video from your prompt…"):
                        video = generate_gradio_media(
                            "text-to-video",
                            HF_TEXT_TO_VIDEO_SPACE,
                            HF_TEXT_TO_VIDEO_API_NAME,
                            HF_TEXT_TO_VIDEO_INPUTS,
                            prompt=prompt.strip(),
                        )
                    st.session_state["media_result"] = {
                        "type": "video",
                        "data": video,
                        "filename": "nexus-ai-prompt-to-video.mp4",
                    }
                    set_feedback(
                        "success",
                        "Video generation complete",
                        "Your prompt-to-video result is ready to preview and download.",
                        transaction={"operation": "prompt-to-video", "space": HF_TEXT_TO_VIDEO_SPACE},
                    )
                except Exception as exc:
                    set_feedback(
                        "error",
                        "Video generation failed",
                        "The configured text-to-video Space could not complete this request.",
                        details=exc,
                    )

    with image_tab:
        st.caption("Upload a starting image and describe how it should move.")
        with st.form("nexus_image_to_video_form"):
            image_prompt = st.text_area(
                "Describe the motion",
                max_chars=2000,
                key="nexus_i2v_prompt",
            )
            uploaded_image = st.file_uploader(
                "Starting image",
                type=["png", "jpg", "jpeg", "webp"],
                key="nexus_i2v_upload",
            )
            image_submitted = st.form_submit_button("Generate from image", width="stretch")
        if image_submitted:
            if uploaded_image is None or not image_prompt.strip():
                set_feedback("error", "Image and prompt required", "Upload a starting image and describe its motion.")
            else:
                try:
                    with tempfile.TemporaryDirectory(prefix="nexus-i2v-") as directory:
                        image_path = Path(directory) / Path(uploaded_image.name).name
                        image_path.write_bytes(uploaded_image.getvalue())
                        with processing_pulse(), st.spinner("Generating image-to-video…"):
                            video = generate_gradio_media(
                                "image-to-video",
                                HF_IMAGE_TO_VIDEO_SPACE,
                                HF_IMAGE_TO_VIDEO_API_NAME,
                                HF_IMAGE_TO_VIDEO_INPUTS,
                                prompt=image_prompt.strip(),
                                image_path=str(image_path),
                            )
                    st.session_state["media_result"] = {
                        "type": "video",
                        "data": video,
                        "filename": "nexus-ai-image-to-video.mp4",
                    }
                    set_feedback(
                        "success",
                        "Image-to-video complete",
                        "Your generated video is ready to preview and download.",
                        transaction={"operation": "image-to-video", "space": HF_IMAGE_TO_VIDEO_SPACE},
                    )
                except Exception as exc:
                    set_feedback(
                        "error",
                        "Image-to-video failed",
                        "The configured image-to-video Space could not complete this request.",
                        details=exc,
                    )

    with long_tab:
        st.caption("Creates six connected five-second scenes, then stitches them into a 30-second MP4.")
        with st.form("nexus_30s_video_form"):
            long_prompt = st.text_area(
                "Describe your video",
                max_chars=4000,
                placeholder="A cinematic journey through a bioluminescent forest at dawn…",
                key="nexus_30s_video_prompt",
            )
            long_submitted = st.form_submit_button("Generate 30-second video", width="stretch")
        if long_submitted:
            if not long_prompt.strip():
                set_feedback("error", "A prompt is required", "Describe the video you want to generate.")
            else:
                try:
                    set_active_theme("Videos", long_prompt)
                    render_active_theme()
                    with processing_pulse(), st.spinner("Creating six scenes and generating the video on Hugging Face…"):
                        video = generate_30s_video(long_prompt.strip())
                    st.session_state["media_result"] = {
                        "type": "video",
                        "data": video,
                        "filename": "nexus-ai-30-second-video.mp4",
                    }
                    set_feedback(
                        "success",
                        "Your video is ready",
                        "All six scenes were generated and stitched into an MP4.",
                        transaction={
                            "operation": "six-scene video generation",
                            "scenes": 6,
                            "seconds_per_scene": 5,
                            "total_duration_seconds": 30,
                        },
                    )
                except Exception as exc:
                    set_feedback(
                        "error",
                        "Video generation failed",
                        "The 30-second cloud pipeline could not finish. Check the configured text-to-video Space and its API input template.",
                        details=exc,
                    )
    if render_result:
        render_media_result()


def render_video_studio_editor() -> None:
    st.caption("Trim a video, add looping background audio, or do both in one render.")
    operation = st.selectbox("Edit", ["Trim video", "Add background audio", "Trim and add audio"])
    with st.form("nexus_video_studio_form"):
        uploaded_video = st.file_uploader(
            "Choose a video",
            type=["mp4", "mov", "mkv", "webm", "avi"],
            key="nexus_studio_video",
        )
        start_seconds = 0.0
        end_seconds = 10.0
        uploaded_audio = None
        volume = 0.25
        if operation in {"Trim video", "Trim and add audio"}:
            start_seconds = st.number_input("Start time (seconds)", min_value=0.0, value=0.0, step=1.0)
            end_seconds = st.number_input("End time (seconds)", min_value=1.0, value=10.0, step=1.0)
        if operation in {"Add background audio", "Trim and add audio"}:
            uploaded_audio = st.file_uploader(
                "Choose background audio",
                type=["mp3", "wav", "m4a", "aac", "ogg"],
                key="nexus_studio_audio",
            )
            volume = st.slider("Background audio volume", min_value=0.0, max_value=1.0, value=0.25, step=0.05)
        submitted = st.form_submit_button("Process video", use_container_width=True)
    if not submitted:
        return
    if uploaded_video is None:
        set_feedback("error", "Choose a video", "Upload a video before starting editing.")
        return
    if operation in {"Add background audio", "Trim and add audio"} and uploaded_audio is None:
        set_feedback("error", "Choose background audio", "Upload an audio track before processing.")
        return

    try:
        with tempfile.TemporaryDirectory(prefix="nexus-studio-") as directory:
            video_path = Path(directory) / Path(uploaded_video.name).name
            video_path.write_bytes(uploaded_video.getvalue())
            output_path = video_path
            if operation in {"Trim video", "Trim and add audio"}:
                output_path = trim_video(video_path, start_seconds, end_seconds)
            if operation in {"Add background audio", "Trim and add audio"}:
                audio_path = Path(directory) / Path(uploaded_audio.name).name
                audio_path.write_bytes(uploaded_audio.getvalue())
                output_path = add_background_audio(output_path, audio_path, volume)
            result = output_path.read_bytes()
            if not result:
                raise RuntimeError("The video editor produced an empty result.")
        st.session_state["media_result"] = {
            "type": "video",
            "data": result,
            "filename": "nexus-ai-edited-video.mp4",
        }
        set_feedback(
            "success",
            "Video editing complete",
            "Your MP4 is ready to preview and download.",
            transaction={
                "operation": operation.lower(),
                "trim_range_seconds": [start_seconds, end_seconds]
                if operation in {"Trim video", "Trim and add audio"}
                else None,
                "background_audio_volume": volume
                if operation in {"Add background audio", "Trim and add audio"}
                else None,
            },
        )
    except Exception as exc:
        set_feedback(
            "error",
            "Video editing failed",
            "NEXUS AI could not process this video. Check the file format and selected options.",
            details=exc,
        )


def render_advanced_video_studio() -> None:
    basic_tab, ai_tab = st.tabs(["Basic Editing", "AI Video-to-Video"])
    with basic_tab:
        st.caption("Trim footage, change playback speed, and optionally mix a background track.")
        with st.form("nexus_advanced_basic_edit_form"):
            uploaded_video = st.file_uploader(
                "Original video",
                type=["mp4", "mov", "mkv", "webm", "avi"],
                key=f"advanced_video_{st.session_state.advanced_video_upload_nonce}",
            )
            start_seconds = st.number_input(
                "Start time (seconds)",
                min_value=0.0,
                value=0.0,
                step=1.0,
                key="advanced_trim_start",
            )
            end_seconds = st.number_input(
                "End time (seconds)",
                min_value=0.1,
                value=10.0,
                step=1.0,
                key="advanced_trim_end",
            )
            speed = st.selectbox("Playback speed", [0.5, 1.0, 1.5, 2.0], index=1)
            audio_track = st.file_uploader(
                "Background audio (optional)",
                type=["mp3", "wav", "m4a", "aac", "ogg"],
                key=f"advanced_audio_{st.session_state.advanced_audio_upload_nonce}",
            )
            audio_volume = st.slider(
                "Background audio volume",
                min_value=0.0,
                max_value=1.0,
                value=0.25,
                step=0.05,
            )
            basic_submitted = st.form_submit_button("Apply basic edits", use_container_width=True)
        if basic_submitted:
            if uploaded_video is None:
                set_feedback("error", "Choose a video", "Upload a video before applying edits.")
            else:
                try:
                    original_bytes = uploaded_video.getvalue()
                    with tempfile.TemporaryDirectory(prefix="nexus-advanced-edit-") as directory:
                        workdir = Path(directory)
                        input_path = workdir / Path(uploaded_video.name).name
                        output_path = workdir / "nexus-edited.mp4"
                        input_path.write_bytes(original_bytes)
                        audio_path = None
                        if audio_track is not None:
                            audio_path = workdir / Path(audio_track.name).name
                            audio_path.write_bytes(audio_track.getvalue())
                        set_active_theme("Advanced Studio")
                        render_active_theme()
                        with processing_pulse(), st.spinner("Trimming, adjusting speed, and mixing audio…"):
                            edited_path = edit_video(
                                input_path,
                                output_path,
                                start_seconds=start_seconds,
                                end_seconds=end_seconds,
                                speed=speed,
                                audio_path=audio_path,
                                audio_volume=audio_volume,
                            )
                        edited_bytes = edited_path.read_bytes()
                        if not edited_bytes:
                            raise RuntimeError("The editor returned an empty output video.")
                    st.session_state["advanced_video_result"] = {
                        "original": original_bytes,
                        "edited": edited_bytes,
                        "filename": "nexus-ai-basic-edited.mp4",
                    }
                    st.session_state.advanced_video_upload_nonce += 1
                    st.session_state.advanced_audio_upload_nonce += 1
                    set_feedback(
                        "success",
                        "Basic video edit complete",
                        "Your edited video is ready to compare and download.",
                        transaction={
                            "operation": "trim and speed adjustment",
                            "trim_seconds": [start_seconds, end_seconds],
                            "playback_speed": f"{speed:g}x",
                            "background_audio": audio_track is not None,
                        },
                    )
                except Exception as exc:
                    set_feedback(
                        "error",
                        "Basic video edit failed",
                        "The video could not be edited. Check its format and selected time range.",
                        details=exc,
                    )

    with ai_tab:
        st.caption(
            "Send your video and style prompt to the configured Hugging Face Space. "
            "The GPU transformation runs remotely."
        )
        if not HF_VIDEO_TO_VIDEO_SPACE:
            st.info("Configure HF_VIDEO_TO_VIDEO_SPACE and its API input template to enable this workflow.")
        with st.form("nexus_ai_video_edit_form"):
            ai_video = st.file_uploader(
                "Video to transform",
                type=["mp4", "mov", "mkv", "webm", "avi"],
                key=f"advanced_ai_video_{st.session_state.advanced_ai_upload_nonce}",
            )
            transformation_prompt = st.text_area(
                "Transformation prompt",
                max_chars=2000,
                placeholder="Convert into a cinematic cyberpunk anime style while preserving the motion…",
            )
            ai_submitted = st.form_submit_button("Transform with AI", use_container_width=True)
        if ai_submitted:
            if ai_video is None or not transformation_prompt.strip():
                set_feedback(
                    "error",
                    "Video and prompt required",
                    "Upload a video and describe the transformation you want.",
                )
            else:
                try:
                    original_bytes = ai_video.getvalue()
                    with tempfile.TemporaryDirectory(prefix="nexus-ai-video-") as directory:
                        input_path = Path(directory) / Path(ai_video.name).name
                        input_path.write_bytes(original_bytes)
                        set_active_theme("Advanced Studio")
                        render_active_theme()
                        with processing_pulse(), st.spinner("Transforming video on Hugging Face Cloud GPU…"):
                            edited_bytes = transform_video_with_hugging_face(
                                input_path,
                                transformation_prompt.strip(),
                                space=HF_VIDEO_TO_VIDEO_SPACE,
                                api_name=HF_VIDEO_TO_VIDEO_API_NAME,
                                inputs_json=HF_VIDEO_TO_VIDEO_INPUTS_JSON,
                            )
                    st.session_state["advanced_video_result"] = {
                        "original": original_bytes,
                        "edited": edited_bytes,
                        "filename": "nexus-ai-transformed-video.mp4",
                    }
                    st.session_state.advanced_ai_upload_nonce += 1
                    set_feedback(
                        "success",
                        "AI transformation complete",
                        "Your transformed video is ready to compare and download.",
                        transaction={
                            "operation": "Hugging Face video-to-video",
                            "space": HF_VIDEO_TO_VIDEO_SPACE,
                        },
                    )
                except Exception as exc:
                    set_feedback(
                        "error",
                        "AI transformation failed",
                        "The configured video-to-video Space could not complete this request.",
                        details=exc,
                    )



# --------------------------------------------------------------------------- #
# Professional video editor (NLE-style workspace)
# --------------------------------------------------------------------------- #
PRO_UI_CSS = """
<style>
.awx-pro-shell [data-testid="stVerticalBlock"] {gap:.55rem;}
.awx-pro-panel {background:linear-gradient(180deg,#12161f 0%,#0c1017 100%);
  border:1px solid rgba(99,102,241,.22); border-radius:14px; padding:.75rem .85rem;}
.awx-pro-title {font-size:.72rem; letter-spacing:.14em; text-transform:uppercase;
  color:#8b95a7; margin:0 0 .35rem 0; font-weight:600;}
.awx-pro-badge {display:inline-flex; align-items:center; gap:.35rem; font-size:.7rem;
  padding:.15rem .55rem; border-radius:999px; background:rgba(124,58,237,.18);
  border:1px solid rgba(167,139,250,.35); color:#c4b5fd;}
.awx-pro-strip {display:flex; gap:.4rem; flex-wrap:wrap;}
.awx-pro-strip span {font-size:.7rem; padding:.2rem .5rem; border-radius:7px;
  background:rgba(255,255,255,.04); border:1px solid rgba(148,163,184,.18); color:#cbd5e1;}
.awx-monitor {border-radius:14px; overflow:hidden; border:1px solid rgba(99,102,241,.25);
  background:#05060a; box-shadow:0 18px 40px -24px rgba(99,102,241,.55);}
.awx-timeline {display:flex; gap:.25rem; margin-top:.4rem;}
.awx-timeline div {flex:1; height:.32rem; border-radius:999px; background:rgba(148,163,184,.18);}
.awx-timeline div.on {background:linear-gradient(90deg,#22d3ee,#7c3aed);}
</style>
"""


def _pro_state_key(name: str) -> str:
    return f"pro_{name}"


def _pro_apply_preset(preset: str) -> None:
    """Fill the colour sliders + curves from a named look."""
    presets = {
        "Neutral": dict(exposure=0, contrast=0, saturation=0, vibrance=0, temperature=0, tint=0, gamma=0,
                        lift=0, gain=0, shadows_hue=0, midtones_hue=0, highlights_hue=0),
        "Teal & Orange": dict(exposure=4, contrast=18, saturation=12, vibrance=22, temperature=14, tint=-4,
                              gamma=2, lift=-6, gain=8, shadows_hue=-14, midtones_hue=4, highlights_hue=16),
        "Film Noir": dict(exposure=-4, contrast=26, saturation=-100, vibrance=0, temperature=-6, tint=0,
                          gamma=-4, lift=-8, gain=6),
        "Golden Hour": dict(exposure=8, contrast=10, saturation=16, vibrance=18, temperature=38, tint=8,
                            gamma=3, lift=4, gain=12, highlights_hue=20),
        "Arctic": dict(exposure=5, contrast=12, saturation=-8, vibrance=6, temperature=-34, tint=-6,
                       gamma=2, lift=2, gain=6),
        "Blockbuster": dict(exposure=6, contrast=24, saturation=8, vibrance=14, temperature=6, tint=0,
                            gamma=1, lift=-4, gain=10, shadows_hue=-8, midtones_hue=2, highlights_hue=10),
    }
    values = presets.get(preset, presets["Neutral"])
    for key, value in values.items():
        st.session_state[_pro_state_key(key)] = value
    st.session_state[_pro_state_key("variant")] = 0


def _pro_section_header(title: str, subtitle: str = "") -> None:
    st.markdown(f'<p class="awx-pro-title">{escape(title)}</p>', unsafe_allow_html=True)
    if subtitle:
        st.caption(subtitle)


def _pro_timeline_strip(seconds: float, fps: float = 0) -> str:
    ticks = 10
    cells = "".join('<div class="on"></div>' if index < ticks else '<div></div>' for index in range(ticks))
    total = time.strftime("%H:%M:%S", time.gmtime(max(seconds, 0)))
    label = f"00:00:00 / {total}" + (f"  ·  {fps:.0f} fps" if fps else "")
    return (
        f'<div class="awx-timeline">{cells}</div>'
        f'<div style="font-size:.68rem;color:#94a3b8;margin-top:.25rem">{label}</div>'
    )


def render_pro_storyboard(source_bytes: bytes, source_name: str | None) -> None:
    """Timeline filmstrip built from evenly spaced frames of the active clip."""
    cache_key = f"pro_story_{hashlib.sha1(source_bytes).hexdigest()[:12]}"
    strips = st.session_state.get(cache_key)
    if strips is None:
        try:
            with tempfile.TemporaryDirectory(prefix="pro-story-") as directory:
                work = Path(directory)
                source_path = work / (source_name or "clip.mp4")
                source_path.write_bytes(source_bytes)
                strips = [path.read_bytes() for path in storyboard(source_path, work, frames=6)]
        except Exception as exc:
            st.caption(f"Filmstrip unavailable: {exc}")
            strips = []
        st.session_state[cache_key] = strips
    if not strips:
        return
    columns = st.columns(len(strips), gap="small")
    for column, frame in zip(columns, strips):
        with column:
            st.image(frame, use_container_width=True)


def render_pro_video_studio() -> None:
    """Full NLE-style editor: media, monitor, inspector, timeline, export, mixer."""
    st.markdown(PRO_UI_CSS, unsafe_allow_html=True)
    nonce = st.session_state.pro_media_nonce
    media = render_pro_media_browser()
    source_bytes = media.get("source_bytes") if media else None
    source_name = media.get("source_name") if media else None
    info = media.get("info") if media else None

    monitor_col, inspector_col, color_col = st.columns([2.5, 1.15, 1.05], gap="medium")

    with monitor_col:
        _pro_section_header("Program monitor", "Preview, scrub and capture the current frame.")
        with st.container(key="awx-pro-monitor"):
            if source_bytes:
                st.video(source_bytes)
                if info:
                    st.markdown(_pro_timeline_strip(info.get("duration", 0), info.get("fps", 0)), unsafe_allow_html=True)
                    render_pro_storyboard(source_bytes, source_name)
            else:
                st.markdown(
                    '<div class="awx-pro-panel" style="min-height:220px;display:flex;align-items:center;'
                    'justify-content:center;color:#64748b;font-size:.85rem">Drop a video into Project media to begin</div>',
                    unsafe_allow_html=True,
                )
        toolbar = st.columns(6, gap="small")
        for column, (icon, label) in zip(
            toolbar,
            [("✂️", "Cut"), ("⏱️", "Trim"), ("🗑️", "Ripple"), ("✂️", "Split"), ("🖼️", "Crop"), ("🔄", "Rotate")],
        ):
            with column:
                st.button(icon, help=label, key=f"pro_tool_{label.lower()}", use_container_width=True, disabled=True)
        st.caption("Trim, crop, rotate, speed and ripple controls live in the Inspector → Video tab.")

    with inspector_col:
        st.markdown('<div class="awx-pro-panel">', unsafe_allow_html=True)
        video_tab, audio_tab, color_tab, speed_tab, tools_tab = st.tabs(
            ["Video", "Audio", "Color", "Speed", "AI Tools"]
        )
        with video_tab:
            _pro_section_header("Transform")
            st.slider("Scale X (%)", 10, 200, key=_pro_state_key("scale_x"))
            st.slider("Scale Y (%)", 10, 200, key=_pro_state_key("scale_y"))
            st.slider("Position X (%)", -100, 100, key=_pro_state_key("position_x"))
            st.slider("Position Y (%)", -100, 100, key=_pro_state_key("position_y"))
            st.slider("Rotate (°)", -180, 180, key=_pro_state_key("rotate"))
            st.selectbox("Aspect ratio", list(PRO_ASPECT_PRESETS), key=_pro_state_key("aspect_ratio"))
            st.selectbox("Scaling", ["fit", "fill", "stretch"], key=_pro_state_key("scale_mode"))
            with st.expander("Crop (percent per edge)"):
                st.slider("Crop top", 0, 45, key=_pro_state_key("crop_top"))
                st.slider("Crop right", 0, 45, key=_pro_state_key("crop_right"))
                st.slider("Crop bottom", 0, 45, key=_pro_state_key("crop_bottom"))
                st.slider("Crop left", 0, 45, key=_pro_state_key("crop_left"))
            st.checkbox("Stabilization", key=_pro_state_key("stabilize"))
            st.checkbox("Motion tracking box", key=_pro_state_key("motion_tracking"))
        with audio_tab:
            _pro_section_header("Audio mixer")
            st.slider("Video volume", 0.0, 2.0, key=_pro_state_key("source_volume"), step=0.05)
            st.slider("Music", 0.0, 2.0, key=_pro_state_key("music_volume"), step=0.05)
            st.slider("Voiceover", 0.0, 2.0, key=_pro_state_key("voiceover_volume"), step=0.05)
            st.slider("SFX", 0.0, 2.0, key=_pro_state_key("sfx_volume"), step=0.05)
            st.slider("Master", 0.0, 2.0, key=_pro_state_key("master_volume"), step=0.05)
            st.checkbox("Duck music under voice", key=_pro_state_key("music_ducking"))
            st.slider("Noise reduction", 0.0, 1.0, key=_pro_state_key("noise_reduction"), step=0.05)
            st.checkbox("Normalize loudness", key=_pro_state_key("normalize"))
            st.caption("Music / voiceover / SFX files are uploaded in the Export tab.")
        with color_tab:
            _pro_section_header("Color", "Primary grade, HSL wheels and curves.")
            preset = st.selectbox("Look preset", list(PRO_COLOR_PRESETS), key=_pro_state_key("color_preset"))
            if st.button("Apply look", key="pro_apply_look", use_container_width=True):
                _pro_apply_preset(preset)
                st.rerun()
            for label, key, low, high in (
                ("Exposure", "exposure", -100, 100),
                ("Contrast", "contrast", -100, 100),
                ("Saturation", "saturation", -100, 100),
                ("Vibrance", "vibrance", -100, 100),
                ("Temperature", "temperature", -100, 100),
                ("Tint", "tint", -100, 100),
                ("Gamma", "gamma", -100, 100),
                ("Lift (shadows)", "lift", -100, 100),
                ("Gain (highlights)", "gain", -100, 100),
                ("Shadows hue", "shadows_hue", -100, 100),
                ("Midtones hue", "midtones_hue", -100, 100),
                ("Highlights hue", "highlights_hue", -100, 100),
            ):
                st.slider(label, low, high, key=_pro_state_key(key))
            curve_channel = st.selectbox("Curves channel", list(PRO_CURVE_CHANNELS), key=_pro_state_key("curve_channel"))
            curve_points = tuple(
                st.slider(f"Point {index + 1}", 0.0, 1.0, key=_pro_state_key(f"curve_{curve_channel}_{index}"), step=0.01)
                for index in range(5)
            )
            st.caption("Shadows / midtones / highlights wheels are the three hue sliders above.")
        with speed_tab:
            _pro_section_header("Time remap")
            st.slider("Playback speed", 0.25, 4.0, key=_pro_state_key("speed"), step=0.05)
            st.checkbox("Reverse clip", key=_pro_state_key("reverse"))
            st.number_input("Freeze frame at (s)", min_value=0.0, key=_pro_state_key("freeze_at"))
            st.number_input("Freeze duration (s)", min_value=0.0, key=_pro_state_key("freeze_seconds"))
            st.slider("Fade in (s)", 0.0, 5.0, key=_pro_state_key("fade_in"), step=0.1)
            st.slider("Fade out (s)", 0.0, 5.0, key=_pro_state_key("fade_out"), step=0.1)
            st.selectbox("Transition", PRO_TRANSITIONS, key=_pro_state_key("transition"))
        with tools_tab:
            _pro_section_header("AI tools")
            st.caption("Auto captions, background removal and scene detection run through the render pipeline.")
            st.checkbox("Auto captions (uses transcript below)", key=_pro_state_key("auto_captions_on"))
            st.text_area("Captions / transcript", key=_pro_state_key("captions_text"), height=90)
            st.checkbox("Background removal (chroma key)", key=_pro_state_key("background_removal"))
            st.color_picker("Key colour", key=_pro_state_key("chroma_key"))
            st.slider("Key tolerance", 0.0, 1.0, key=_pro_state_key("chroma_tolerance"), step=0.05)
            st.checkbox("Face blur", key=_pro_state_key("face_blur"))
            st.checkbox("Silence removal", key=_pro_state_key("silence_removal"))
        st.markdown("</div>", unsafe_allow_html=True)

    with color_col:
        st.markdown('<div class="awx-pro-panel">', unsafe_allow_html=True)
        _pro_section_header("Filters & masks")
        st.selectbox("Filter / LUT", PRO_FILTERS, key=_pro_state_key("filter_preset"))
        st.slider("Filter strength", 0.0, 1.0, key=_pro_state_key("filter_strength"), step=0.05)
        st.selectbox("Blur", PRO_BLURS, key=_pro_state_key("blur"))
        st.slider("Blur radius", 0.0, 40.0, key=_pro_state_key("blur_radius"), step=0.5)
        st.selectbox("Mask shape", PRO_MASKS, key=_pro_state_key("mask_shape"))
        st.slider("Feather", 0.0, 1.0, key=_pro_state_key("mask_feather"), step=0.05)
        st.color_picker("Canvas fill", key=_pro_state_key("background_fill"))
        st.markdown("</div>", unsafe_allow_html=True)

    st.divider()
    left_col, right_col = st.columns([2.2, 1.0], gap="medium")
    with left_col:
        if info:
            st.markdown(
                '<div class="awx-pro-strip">'
                + "".join(
                    f"<span>{escape(str(key))}: {escape(str(value))}</span>"
                    for key, value in {
                        "resolution": f"{info['width']}×{info['height']}",
                        "duration": f"{info['duration']:.1f}s",
                        "fps": f"{info['fps']:.0f}",
                        "audio": "yes" if info["has_audio"] else "no",
                    }.items()
                )
                + "</div>",
                unsafe_allow_html=True,
            )
        with st.expander("Timeline tracks (V3 overlay · V2 video · V1 video · A1 music · A2 SFX · A3 voiceover)"):
            st.caption(
                "The renderer blends V1 + V2, applies titles/overlays on V3 and mixes A1–A3 with ducking. "
                "Upload the matching files in Export to activate each track."
            )
            st.markdown(_pro_timeline_strip(info.get("duration", 0) if info else 0), unsafe_allow_html=True)
        if info:
            estimate = _estimate_current_render(info)
            if estimate:
                seconds, stages, pixels = estimate
                note = f"Estimated render: ~{seconds:.0f}s · {stages} heavy stage(s) · {pixels:,} px frame"
                if seconds > 120:
                    st.warning(note + " — long render, consider a lower export resolution.")
                else:
                    st.caption(note)
    with right_col:
        st.markdown('<div class="awx-pro-panel">', unsafe_allow_html=True)
        _pro_section_header("Export")
        with st.form("pro_export_form"):
            st.selectbox("Format", list(PRO_EXPORT_FORMATS), key=_pro_state_key("export_format"))
            st.selectbox("Resolution", list(PRO_RESOLUTIONS), key=_pro_state_key("resolution"))
            st.selectbox("Frame rate", list(PRO_FPS), key=_pro_state_key("fps"))
            st.selectbox("Bitrate", list(PRO_BITRATES), key=_pro_state_key("bitrate"))
            st.slider("Quality", 10.0, 100.0, key=_pro_state_key("quality"), step=1.0)
            st.selectbox("Aspect ratio", list(PRO_ASPECT_PRESETS), key=_pro_state_key("export_aspect"))
            st.file_uploader("Music track (A1)", type=[s.lstrip(".") for s in PRO_AUDIO_SUFFIXES], key=f"pro_music_{nonce}")
            st.file_uploader("SFX (A2)", type=[s.lstrip(".") for s in PRO_AUDIO_SUFFIXES], key=f"pro_sfx_{nonce}")
            st.file_uploader("Voiceover (A3)", type=[s.lstrip(".") for s in PRO_AUDIO_SUFFIXES], key=f"pro_voice_{nonce}")
            st.file_uploader("Subtitles (.srt / .vtt)", type=["srt", "vtt"], key=f"pro_subs_{nonce}")
            st.file_uploader("Logo / watermark", type=["png", "jpg", "jpeg", "webp"], key=f"pro_logo_{nonce}")
            st.text_input("Watermark text", key=_pro_state_key("watermark_text"))
            st.slider("Watermark opacity", 0.0, 1.0, key=_pro_state_key("watermark_opacity"), step=0.05)
            st.selectbox("Watermark corner", ["bottom-right", "bottom-left", "top-right", "top-left", "center"], key=_pro_state_key("watermark_position"))
            export_clicked = st.form_submit_button("⬇ Export video", use_container_width=True, type="primary")
        st.markdown("</div>", unsafe_allow_html=True)

    titles_key = "pro_titles"
    st.session_state.setdefault(titles_key, [])
    if export_clicked:
        _run_pro_render(
            source_bytes=source_bytes,
            source_name=source_name,
            form_state={key: st.session_state.get(key) for key in st.session_state},
        )

    render_pro_export_result()


def _estimate_current_render(info: dict[str, Any]) -> tuple[float, int, int] | None:
    """Dry-run the current inspector settings to preview export cost (never renders)."""
    try:
        from backend.pro_video_editor import RESOLUTION_PRESETS as _presets, VideoInfo

        spec = _pro_build_spec(Path("preview.mp4"), Path("preview-out.mp4"), {}, {})
        spec.max_duration_seconds = 0
        clip_info = VideoInfo(
            duration=float(info.get("duration", 10.0)),
            fps=float(info.get("fps", 30.0)),
            width=int(info.get("width", 1280)),
            height=int(info.get("height", 720)),
            has_audio=bool(info.get("has_audio")),
        )
        seconds, stages = estimate_render_seconds(spec, info=clip_info)
        preset = _presets.get(spec.resolution)
        pixels = preset[0] * preset[1] if preset else clip_info.width * clip_info.height
        return seconds, stages, pixels
    except Exception:
        return None


def render_pro_media_browser() -> dict[str, Any] | None:
    """Project media panel: upload, AI results, previous renders and recent clips."""
    nonce = st.session_state.pro_media_nonce
    st.markdown('<div class="awx-pro-panel">', unsafe_allow_html=True)
    _pro_section_header("Project media")
    uploaded = st.file_uploader(
        "Import video",
        type=[s.lstrip(".") for s in PRO_VIDEO_SUFFIXES],
        key=f"pro_source_{nonce}",
        label_visibility="collapsed",
    )
    st.markdown("</div>", unsafe_allow_html=True)

    if uploaded is not None:
        payload = uploaded.getvalue()
        st.session_state["pro_source_name"] = Path(uploaded.name).name
        st.session_state["pro_source_bytes"] = payload
        st.session_state["pro_render_info"] = None

    if st.session_state.get("pro_source_bytes"):
        name = st.session_state.get("pro_source_name") or "clip.mp4"
        st.markdown(
            f'<div class="awx-pro-panel"><span class="awx-pro-badge">● {escape(name)}</span>'
            f'<div style="color:#64748b;font-size:.7rem;margin-top:.3rem">Active on timeline</div></div>',
            unsafe_allow_html=True,
        )

    info = None
    source_bytes = st.session_state.get("pro_source_bytes")
    if source_bytes:
        try:
            with tempfile.TemporaryDirectory(prefix="pro-probe-") as directory:
                probe_path = Path(directory) / (st.session_state.get("pro_source_name") or "clip.mp4")
                probe_path.write_bytes(source_bytes)
                info = probe_video(probe_path).as_dict()
        except Exception as exc:
            st.caption(f"Preview info unavailable: {exc}")

    catalog = []
    ai_result = st.session_state.get("media_result")
    if ai_result and ai_result.get("type") == "video":
        catalog.append({"name": "AI video result", "kind": "AI", "data": ai_result["data"], "mime": "video/mp4"})
    edited = st.session_state.get("advanced_video_result")
    if edited and edited.get("edited"):
        catalog.append({"name": edited.get("filename", "Edited"), "kind": "Render", "data": edited["edited"], "mime": "video/mp4"})
    rendered = st.session_state.get("pro_rendered")
    if rendered:
        catalog.append({"name": rendered.get("filename", "Pro render"), "kind": "Render", "data": rendered["data"], "mime": rendered["mime"]})

    if catalog:
        _pro_section_header("Recent renders")
        columns = st.columns(min(len(catalog), 3))
        for column, item in zip(columns, catalog[:3]):
            with column:
                st.caption(f"{item['kind']} · {item['name']}")
                if st.button("Use this clip", key=f"pro_use_{item['name']}_{nonce}", use_container_width=True):
                    st.session_state["pro_source_bytes"] = item["data"]
                    st.session_state["pro_source_name"] = Path(item["name"]).name or "clip.mp4"
                    st.rerun()

    if info:
        return {"source_bytes": source_bytes, "source_name": st.session_state.get("pro_source_name"), "info": info}
    return None


def _pro_build_spec(
    source_path: Path,
    output_path: Path,
    state: dict[str, Any],
    media_paths: dict[str, str | None],
) -> ProEditSpec:
    get = lambda name, default=None: st.session_state.get(_pro_state_key(name), default)
    curve_channel = get("curve_channel", "master")
    curves = {
        key: tuple(float(st.session_state.get(_pro_state_key(f"curve_{channel}_{index}"), index / 4)) for index in range(5))
        for channel, key in (("master", "curve_master"), ("red", "curve_red"), ("green", "curve_green"), ("blue", "curve_blue"))
    }
    curves[curve_channel] = tuple(
        float(st.session_state.get(_pro_state_key(f"curve_{curve_channel}_{index}"), index / 4)) for index in range(5)
    )

    grade = ColorGrade(
        exposure=float(get("exposure", 0)), contrast=float(get("contrast", 0)),
        saturation=float(get("saturation", 0)), vibrance=float(get("vibrance", 0)),
        temperature=float(get("temperature", 0)), tint=float(get("tint", 0)),
        gamma=float(get("gamma", 0)), lift=float(get("lift", 0)), gain=float(get("gain", 0)),
        shadows_hue=float(get("shadows_hue", 0)), midtones_hue=float(get("midtones_hue", 0)),
        highlights_hue=float(get("highlights_hue", 0)),
        curve_master=curves["curve_master"], curve_red=curves["curve_red"],
        curve_green=curves["curve_green"], curve_blue=curves["curve_blue"],
    )
    crop_edges = tuple(float(get(f"crop_{edge}", 0)) for edge in ("top", "right", "bottom", "left"))
    transform = TransformSpec(
        scale_x=float(get("scale_x", 100)), scale_y=float(get("scale_y", 100)),
        position_x=float(get("position_x", 0)), position_y=float(get("position_y", 0)),
        rotate=float(get("rotate", 0)), crop=crop_edges if any(crop_edges) else None,
        aspect_ratio=str(get("export_aspect", "Original")), scale_mode=str(get("scale_mode", "fit")),
    )
    audio = AudioMixer(
        source_volume=float(get("source_volume", 1.0)), music_volume=float(get("music_volume", 0.7)),
        voiceover_volume=float(get("voiceover_volume", 1.0)), sfx_volume=float(get("sfx_volume", 0.8)),
        master_volume=float(get("master_volume", 1.0)), music_path=media_paths.get("music"),
        voiceover_path=media_paths.get("voice"), sfx_path=media_paths.get("sfx"),
        music_ducking=bool(get("music_ducking", True)), noise_reduction=float(get("noise_reduction", 0.0)),
        normalize=bool(get("normalize", True)),
    )
    captions = get("captions_text") if get("auto_captions_on") else None
    titles = [
        {"text": entry.get("text", ""), "start": float(entry.get("start", 0)), "duration": float(entry.get("duration", 3)),
         "color": entry.get("color", "#ffffff"), "position": ("center", "center")}
        for entry in st.session_state.get("pro_titles", [])
        if str(entry.get("text", "")).strip()
    ]
    overlays = OverlaySpec(
        titles=titles, subtitles_path=media_paths.get("subtitles"),
        watermark_text=(get("watermark_text") or None), watermark_path=media_paths.get("logo"),
        watermark_opacity=float(get("watermark_opacity", 0.6)),
        watermark_position=str(get("watermark_position", "bottom-right")), auto_captions=captions,
    )
    return ProEditSpec(
        source_path=str(source_path), output_path=str(output_path),
        speed=float(get("speed", 1.0)), reverse=bool(get("reverse", False)),
        freeze_at=(float(get("freeze_at") or 0) or None), freeze_seconds=float(get("freeze_seconds", 0)),
        fade_in=float(get("fade_in", 0)), fade_out=float(get("fade_out", 0)),
        transition=str(get("transition", "None")), filter_preset=str(get("filter_preset", "None")),
        filter_strength=float(get("filter_strength", 1.0)), blur=str(get("blur", "None")),
        blur_radius=float(get("blur_radius", 0)), stabilize=bool(get("stabilize", False)),
        motion_tracking=bool(get("motion_tracking", False)),
        track_box=(0.35, 0.3, 0.3, 0.4) if get("motion_tracking") else None,
        mask_shape=str(get("mask_shape", "None")), mask_feather=float(get("mask_feather", 0.2)),
        chroma_key=(get("chroma_key") if get("background_removal") else None),
        chroma_tolerance=float(get("chroma_tolerance", 0.2)), face_blur=bool(get("face_blur", False)),
        background_removal=bool(get("background_removal", False)),
        resolution=str(get("resolution", "1080p")), fps=str(get("fps", "30")),
        bitrate=str(get("bitrate", "High (Recommended)")), quality=float(get("quality", 80)),
        export_format=str(get("export_format", "MP4")), background_fill=str(get("background_fill", "#05060a")),
        color=grade, transform=transform, audio=audio, overlays=overlays,
    )


def _run_pro_render(source_bytes: bytes | None, source_name: str | None, form_state: dict[str, Any]) -> None:
    if not source_bytes:
        set_feedback("error", "Import a video", "Drop a video into Project media before exporting.")
        return
    nonce = st.session_state.pro_media_nonce
    with tempfile.TemporaryDirectory(prefix="auralis-pro-") as directory:
        work = Path(directory)
        source_path = work / (source_name or "source.mp4")
        source_path.write_bytes(source_bytes)
        media_paths: dict[str, str | None] = {}
        for key, suffix in (("music", "music.mp3"), ("sfx", "sfx.wav"), ("voice", "voice.wav")):
            media_paths[key] = _pro_persist_upload(st.session_state.get(f"pro_{key}_{nonce}"), work, suffix)
        media_paths["subtitles"] = _pro_persist_upload(st.session_state.get(f"pro_subs_{nonce}"), work, "captions.srt")
        logo = _pro_persist_upload(st.session_state.get(f"pro_logo_{nonce}"), work, "logo.png")
        media_paths["logo"] = logo
        extension = PRO_EXPORT_FORMATS.get(st.session_state.get(_pro_state_key("export_format"), "MP4"), ".mp4")
        output_path = work / f"auralis-pro-render{extension}"
        spec = _pro_build_spec(source_path, output_path, form_state, media_paths)
        try:
            set_active_theme("Advanced Studio")
            render_active_theme()
            with processing_pulse(), st.spinner("Rendering with colour, transforms, effects and audio mixer…"):
                result_path = render_pro_video(spec)
            payload = result_path.read_bytes()
        except Exception as exc:
            set_feedback("error", "Export failed", str(exc) if isinstance(exc, ProEditError) else "The render could not be completed.", details=exc)
            return
    mime = {".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm", ".gif": "image/gif"}.get(
        extension, "video/mp4"
    )
    st.session_state["pro_rendered"] = {"data": payload, "filename": f"auralis-pro-render{extension}", "mime": mime}
    st.session_state["pro_media_nonce"] = nonce + 1
    st.session_state["pro_render_info"] = {"operations": plan_operations(spec), "bytes": len(payload)}
    set_feedback(
        "success",
        "Export complete",
        "Your rendered clip is ready to preview and download.",
        transaction={"operation": "professional render", "steps": len(st.session_state["pro_render_info"]["operations"])},
    )
    st.rerun()


def _pro_persist_upload(uploaded: Any, directory: Path, fallback_name: str) -> str | None:
    if uploaded is None:
        return None
    target = directory / (Path(getattr(uploaded, "name", fallback_name)).name or fallback_name)
    try:
        target.write_bytes(uploaded.getvalue())
    except Exception:
        return None
    return str(target)


def render_pro_export_result() -> None:
    rendered = st.session_state.get("pro_rendered")
    if not rendered:
        return
    st.divider()
    st.markdown("### Rendered output")
    info = st.session_state.get("pro_render_info") or {}
    if info.get("operations"):
        st.markdown(
            '<div class="awx-pro-strip">'
            + "".join(f"<span>{escape(str(op))}</span>" for op in info["operations"])
            + "</div>",
            unsafe_allow_html=True,
        )
    if rendered["mime"].startswith("video"):
        st.video(rendered["data"])
    else:
        st.image(rendered["data"], use_container_width=True)
    action_col, clear_col = st.columns(2)
    with action_col:
        st.download_button(
            "Download render",
            data=rendered["data"],
            file_name=rendered["filename"],
            mime=rendered["mime"],
            use_container_width=True,
            key="pro_download_render",
        )
    with clear_col:
        if st.button("Clear render", key="pro_clear_render", use_container_width=True):
            st.session_state.pop("pro_rendered", None)
            st.session_state.pop("pro_render_info", None)
            st.rerun()


def render_advanced_video_comparison() -> None:
    result = st.session_state.get("advanced_video_result")
    if not result:
        return
    st.divider()
    st.markdown("### Original vs Edited")
    original_column, edited_column = st.columns(2)
    with original_column:
        st.markdown("#### Original")
        st.video(result["original"])
    with edited_column:
        st.markdown("#### Edited")
        st.video(result["edited"])
        if st.button("🔍 Full Screen View", key="open_advanced_fullscreen", use_container_width=True):
            st.session_state["fullscreen_media"] = {
                "data": result["edited"],
                "type": "video",
            }
            st.rerun()
        st.download_button(
            "Download edited video",
            data=result["edited"],
            file_name=result["filename"],
            mime="video/mp4",
            key="download_advanced_edited_video",
            use_container_width=True,
        )
    if st.button("Clear video previews from memory", key="clear_advanced_video_result"):
        st.session_state.pop("advanced_video_result", None)
        st.rerun()


def save_conversation_turn(prompt: str, response: str, files: list[str], voice_response: bool = False) -> None:
    owner_identifier = current_user_identifier()
    if not owner_identifier:
        return
    now = datetime.now(timezone.utc)
    conversation_id = str(st.session_state.get("chat_id", ""))
    title = str(st.session_state.get("chat_title", "New Chat"))
    try:
        get_conversations_collection().update_one(
            {"user_id": owner_identifier, "conversation_id": conversation_id},
            {
                "$set": {
                    "user_id": owner_identifier,
                    "username": current_username(),
                    "conversation_id": conversation_id,
                    "title": title,
                    "updated_at": now,
                },
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )
        get_conversation_messages_collection().insert_many(
            [
                {
                    "user_id": owner_identifier,
                    "conversation_id": conversation_id,
                    "role": "user",
                    "content": prompt,
                    "files": files,
                    "created_at": now,
                },
                {
                    "user_id": owner_identifier,
                    "conversation_id": conversation_id,
                    "role": "assistant",
                    "content": response,
                    "voice_response": voice_response,
                    "created_at": now,
                },
            ]
        )
        get_conversation_summaries_collection().update_one(
            {"user_id": owner_identifier, "conversation_id": conversation_id},
            {"$set": {"summary": prompt[:240], "updated_at": now}, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )
    except Exception as exc:
        set_connection_failure(
            exc,
            endpoint="mongodb://Atlas",
            payload={"collections": ["conversations", "conversation_messages", "conversation_summaries"], "operation": "save_conversation_turn"},
            title="Conversation could not be saved",
            message="Your response is available, but saving conversation history failed.",
        )


def save_chat_turn(prompt: str, response: str, files: list[str], voice_response: bool = False) -> None:
    st.session_state.messages.extend([
        {"role": "user", "content": prompt, "files": files},
        {"role": "assistant", "content": response, "voice_response": voice_response},
    ])
    if st.session_state.chat_title == "New Chat":
        st.session_state.chat_title = prompt[:48] or "New Chat"
    # Keep the persisted canvas selection separate from the selectbox widget
    # state. The widget may already have been instantiated earlier in this run.
    st.session_state["artifact_source"] = "Latest response"
    save_record({
        "chat_id": st.session_state.chat_id,
        "title": st.session_state.chat_title,
        "messages": st.session_state.messages,
        "prompt": prompt,
        "response": response,
        "type": "chat",
    })
    save_conversation_turn(prompt, response, files, voice_response)


def clear_chat_attachments() -> None:
    st.session_state.file_input_nonce += 1
    st.session_state.audio_input_nonce += 1
    st.rerun()


def render_assistant_actions(
    message_index: int,
    response: str,
    previous_prompt: str,
) -> None:
    action_columns = st.columns([0.75, 0.75, 0.75, 1.2], gap="small")
    serialized_text = json.dumps(response).replace("<", "\\u003c")
    action_id = f"assistant-action-{message_index}"
    with action_columns[0]:
        st.html(
            f"""
            <button id="{action_id}-copy" class="assistant-action"
              title="Copy response">⧉ Copy</button>
            <script>
              (() => {{
                const copyButton = document.getElementById("{action_id}-copy");
                copyButton.addEventListener("click", async () => {{
                  try {{
                    await navigator.clipboard.writeText({serialized_text});
                    copyButton.textContent = "Copied";
                  }} catch {{
                    copyButton.textContent = "Copy unavailable";
                  }}
                }});
              }})();
            </script>
            <style>
              .assistant-action {{ width:100%; padding:.35rem .5rem; border:1px solid rgba(148,163,184,.2);
                border-radius:9px; color:#cbd5e1; background:rgba(255,255,255,.035); cursor:pointer; }}
              .assistant-action:hover {{ border-color:rgba(34,211,238,.5); color:#fff; }}
            </style>
            """,
            width="stretch",
            unsafe_allow_javascript=True,
        )
    with action_columns[1]:
        speak_text = json.dumps(response).replace("<", "\\u003c")
        st.html(
            f"""
            <button id="{action_id}-speak" class="assistant-action"
              title="Speak response">🔊 Speak</button>
            <script>
              (() => {{
                const button = document.getElementById("{action_id}-speak");
                button.addEventListener("click", () => {{
                  if (!("speechSynthesis" in window)) return;
                  window.speechSynthesis.cancel();
                  window.speechSynthesis.speak(new SpeechSynthesisUtterance({speak_text}));
                }});
              }})();
            </script>
            """,
            width="stretch",
            unsafe_allow_javascript=True,
        )
    with action_columns[2]:
        if st.button(
            "Retry",
            key=f"retry_response_{message_index}",
            icon=":material/refresh:",
            width="stretch",
            disabled=not previous_prompt,
        ):
            st.session_state["retry_prompt"] = previous_prompt
            st.rerun()
    with action_columns[3]:
        feedback_columns = st.columns(2, gap="small")
        with feedback_columns[0]:
            if st.button(
                "👍",
                key=f"positive_feedback_{message_index}",
                help="Helpful response",
                width="stretch",
            ):
                st.toast("Thanks for the feedback.", icon="✅")
        with feedback_columns[1]:
            if st.button(
                "👎",
                key=f"negative_feedback_{message_index}",
                help="Needs improvement",
                width="stretch",
            ):
                st.toast("Thanks, we’ll use this to improve.", icon="💬")


def _render_artifact_file(artifact: dict[str, Any]) -> None:
    name = str(artifact["name"])
    extension = str(artifact["extension"])
    kind = str(artifact["kind"])
    content = artifact["content"]
    raw_data = artifact["data"]

    st.markdown(f"**{escape(name)}**")
    st.caption(f"{extension.upper()} preview · uploaded for this conversation")
    if kind == "image":
        st.image(content, alt=f"Preview of {name}", width="stretch")
        if st.button(
            "Open full screen",
            key=f"artifact_fullscreen_{hashlib.sha1(name.encode()).hexdigest()[:10]}",
            icon=":material/open_in_full:",
            width="stretch",
        ):
            st.session_state["fullscreen_media"] = {"data": raw_data, "type": "image"}
            st.rerun()
    elif kind == "pdf":
        st.pdf(raw_data, height=560, alt=f"PDF preview of {name}")
    elif kind == "docx":
        for paragraph in content["paragraphs"]:
            st.markdown(paragraph)
        for table_index, rows in enumerate(content["tables"], start=1):
            st.caption(f"Table {table_index}")
            st.table(rows[:40], width="stretch")
    elif kind == "xlsx":
        sheet_names = list(content)
        if sheet_names:
            selected_sheet = st.selectbox(
                "Worksheet",
                sheet_names,
                key=f"artifact_sheet_{hashlib.sha1(name.encode()).hexdigest()[:10]}",
            )
            st.dataframe(
                content[selected_sheet],
                height=300,
                width="stretch",
                alt=f"Spreadsheet preview of {name}, {selected_sheet}",
            )
    elif kind == "csv":
        st.dataframe(content, height=300, width="stretch", alt=f"CSV preview of {name}")
    elif kind == "pptx":
        if content:
            slide_labels = [f"Slide {index}" for index in range(1, len(content) + 1)]
            selected_slide_label = st.selectbox(
                "Slide",
                slide_labels,
                key=f"artifact_slide_{hashlib.sha1(name.encode()).hexdigest()[:10]}",
            )
            slide = content[slide_labels.index(selected_slide_label)]
            for paragraph in slide["paragraphs"]:
                st.markdown(paragraph)
            for table_index, rows in enumerate(slide["tables"], start=1):
                st.caption(f"Table {table_index}")
                st.table(rows[:40], width="stretch")
        else:
            st.caption("This presentation contains no text slides.")
    elif kind == "code":
        st.code(content, language=extension if extension in {"py", "js", "html", "css", "json"} else None)
    st.download_button(
        "Download original",
        key=f"artifact_download_{hashlib.sha1(name.encode()).hexdigest()[:10]}",
        data=raw_data,
        file_name=name,
        mime="application/octet-stream",
        icon=":material/download:",
        width="stretch",
    )


def render_chat_file_quick_actions() -> None:
    """In-chat entry points for file work (no separate PDF/Docs/Office pages).

    Everything here produces a real file with the same generators the backend
    uses; nothing is faked when a generator library is unavailable.
    """
    st.caption("Everything file-related happens in this chat — upload, analyse, convert or generate.")
    with st.expander("Create a file from this chat (PDF · Word · Excel · PowerPoint · CSV)", expanded=False):
        with st.form("chat_file_tools_form"):
            file_kind = st.selectbox(
                "Output format",
                ["PDF", "Word (.docx)", "Excel (.xlsx)", "PowerPoint (.pptx)", "CSV"],
                key="chat_file_tools_format",
            )
            file_title = st.text_input(
                "Document title",
                key="chat_file_tools_title",
                placeholder="Q4 Marketing Plan",
            )
            file_prompt = st.text_area(
                "What should it contain?",
                key="chat_file_tools_prompt",
                placeholder="Summarise the key results, risks and next steps from this conversation…",
                height=90,
            )
            make_file = st.form_submit_button("Generate file", use_container_width=True, type="primary")
        if make_file:
            if not file_prompt.strip():
                set_feedback("error", "Describe the document", "Add what the file should contain.")
            else:
                render_chat_file_generation(file_kind, file_title.strip(), file_prompt.strip())

        # history_titles() returns a list of title strings, not (title, metadata)
        # pairs. Unpacking each title caused the in-chat file action to crash with
        # "too many values to unpack (expected 2)".
        recent_conversation = next(
            (
                title
                for title in history_titles()
                if title == st.session_state.get("chat_title")
            ),
            None,
        )
        if st.button(
            "Summarise this conversation into a document",
            key="chat_file_tools_summarise",
            icon=":material/summarize:",
            use_container_width=True,
        ):
            transcript = "\n".join(
                f"{message['role'].title()}: {message['content']}"
                for message in st.session_state.get("messages", [])
                if not message.get("generated_files")
            )
            if not transcript.strip():
                set_feedback("error", "Nothing to summarise", "Send a few messages first.")
            elif not recent_conversation:
                set_feedback("error", "Conversation not saved", "Save the chat before exporting it.")
            else:
                render_chat_file_generation(
                    "Word (.docx)",
                    st.session_state.get("chat_title", "Conversation"),
                    "Turn this conversation into a structured document.\n\n" + transcript[-6000:],
                )


def render_chat_file_generation(file_kind: str, title: str, prompt: str) -> None:
    formats = {
        "PDF": "pdf",
        "Word (.docx)": "docx",
        "Excel (.xlsx)": "xlsx",
        "PowerPoint (.pptx)": "pptx",
        "CSV": "csv",
    }
    fmt = formats.get(file_kind, "docx")
    title = title or prompt.strip()[:60]
    with st.spinner(f"Generating a real .{fmt} file…"):
        try:
            backend_status, payload = backend_post_json(
                "/api/v1/chat/v2/documents/generate",
                {"prompt": prompt, "format": fmt, "title": title},
            )
        except Exception as exc:
            set_feedback(
                "error",
                "Document generation failed",
                "The authoring service could not be reached.",
                details=exc,
            )
            return
    if backend_status == 503:
        set_feedback(
            "error",
            "AI authoring is not configured",
            (payload or {}).get("detail", "Configure an AI provider to generate documents."),
        )
        return
    if backend_status != 200 or not isinstance(payload, dict) or not payload.get("data_base64"):
        detail = (payload or {}).get("detail") if isinstance(payload, dict) else None
        set_feedback(
            "error",
            "Document generation failed",
            detail or f"The server answered with HTTP {backend_status}.",
        )
        return
    import base64 as _base64

    data = _base64.b64decode(payload["data_base64"])
    generated = {
        "name": payload.get("filename", f"{title}.{fmt}"),
        "extension": fmt,
        "kind": fmt,
        "content": data,
        "mime": FILE_MIME_TYPES.get(fmt, "application/octet-stream"),
    }
    st.session_state.messages.append(
        {
            "role": "assistant",
            "content": f"Created **{generated['name']}** ({len(data) // 1024} KB) from your request.",
            "generated_files": [generated],
        }
    )
    render_generated_file_card(generated)
    set_feedback(
        "success",
        "File generated",
        "The document is attached to this conversation with preview and download.",
        transaction={"operation": f"chat file generation ({fmt})", "bytes": len(data)},
    )


FILE_MIME_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "csv": "text/csv",
    "md": "text/markdown",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
}


def render_generated_file_card(generated: dict[str, Any]) -> None:
    """Inline preview + download for a file produced inside the chat."""
    name = str(generated.get("name", "file"))
    extension = str(generated.get("extension") or name.rsplit(".", 1)[-1]).lower()
    data = generated.get("content")
    if not isinstance(data, (bytes, bytearray)):
        st.warning(f"{name} has no stored preview.")
        return
    with st.container(border=True):
        st.markdown(f"**📄 {escape(name)}**")
        if extension in {"png", "jpg", "jpeg", "webp"}:
            st.image(bytes(data), alt=name, width="stretch")
        elif extension in {"md", "csv", "txt", "py", "json"}:
            preview = bytes(data).decode("utf-8", errors="replace")[:2000]
            if extension == "md":
                st.markdown(preview)
            else:
                st.code(preview, language=extension)
        elif extension == "pdf":
            st.caption("PDF ready — download to open in your reader.")
        else:
            st.caption(f"{extension.upper()} file ready — download to open in Office.")
        st.download_button(
            "Download",
            data=bytes(data),
            file_name=name,
            mime=generated.get("mime") or FILE_MIME_TYPES.get(extension, "application/octet-stream"),
            key=f"download_generated_{name}_{len(data)}",
            use_container_width=True,
        )


def backend_post_json(path: str, payload: dict[str, Any]) -> tuple[int, Any]:
    """Call the FastAPI backend with the signed-in user's token."""
    base = (os.getenv("BACKEND_URL", "") or os.getenv("API_BASE_URL", "")).rstrip("/")
    if not base:
        return 0, {"detail": "Backend URL is not configured (BACKEND_URL)."}
    token = st.session_state.get("auth_session_token") or st.session_state.get("token")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        response = requests.post(f"{base}{path}", json=payload, headers=headers, timeout=180)
    except requests.RequestException as exc:
        return 0, {"detail": f"Backend unreachable: {exc}"}
    try:
        body = response.json()
    except ValueError:
        body = {"detail": response.text[:400]}
    return response.status_code, body


def render_artifact_panel() -> None:
    with st.container(key="nexus-artifact-panel", border=True):
        heading_columns = st.columns([1, 0.24], vertical_alignment="center")
        with heading_columns[0]:
            st.markdown("<div class='artifact-panel-heading'>Artifact canvas</div>", unsafe_allow_html=True)
            st.caption("Preview files and the latest AI response.")
        with heading_columns[1]:
            if st.button(
                "×",
                key="close_artifact_panel",
                help="Close canvas",
                type="secondary",
                width="content",
            ):
                st.session_state["artifact_panel_open"] = False
                st.rerun()

        artifacts = st.session_state.get("artifact_files", [])
        latest_response = next(
            (
                message.get("content", "")
                for message in reversed(st.session_state.get("messages", []))
                if message.get("role") == "assistant"
            ),
            "",
        )
        options = ["Latest response", *[artifact["name"] for artifact in artifacts]]
        if st.session_state.get("artifact_source") not in options:
            st.session_state["artifact_source"] = "Latest response"
        if st.session_state.get("artifact_source_widget") not in options:
            st.session_state["artifact_source_widget"] = st.session_state["artifact_source"]
        selected_source = st.selectbox(
            "Canvas source",
            options,
            key="artifact_source_widget",
            label_visibility="collapsed",
        )
        st.session_state["artifact_source"] = selected_source
        if selected_source == "Latest response":
            result = st.session_state.get("media_result")
            if result:
                if result["type"] == "image":
                    st.image(result["data"], alt="Latest generated image", width="stretch")
                    if st.button(
                        "Open full screen",
                        key="canvas_media_fullscreen",
                        icon=":material/open_in_full:",
                        width="stretch",
                    ):
                        st.session_state["fullscreen_media"] = {
                            "data": result["data"],
                            "type": "image",
                        }
                        st.rerun()
                    st.download_button(
                        "Download image",
                        data=result["data"],
                        file_name=result["filename"],
                        mime="image/png",
                        width="stretch",
                    )
                    if st.button(
                        "Create image variation",
                        key="create_image_variation",
                        icon=":material/auto_awesome:",
                        width="stretch",
                    ):
                        prompt = st.session_state.get("last_image_prompt", "").strip()
                        if prompt:
                            try:
                                with st.spinner("Creating an image variation…"):
                                    varied_image = generate_hf_image(prompt, 1024, 1024)
                                st.session_state["media_result"] = {
                                    "type": "image",
                                    "data": varied_image,
                                    "filename": "auralis-image-variation.png",
                                }
                                st.rerun()
                            except Exception as exc:
                                error_title, error_message = describe_hugging_face_error(exc)
                                set_feedback("error", error_title, error_message, details=exc)
                elif result["type"] == "video":
                    st.video(result["data"], format="video/mp4")
                    st.download_button(
                        "Download video",
                        data=result["data"],
                        file_name=result["filename"],
                        mime="video/mp4",
                        width="stretch",
                    )
            if latest_response:
                st.markdown(latest_response)
                st.download_button(
                    "Download response as Markdown",
                    data=latest_response,
                    file_name="auralis-response.md",
                    mime="text/markdown",
                    width="stretch",
                )
            elif not result:
                st.info("Generated media and AI responses will appear here.")
        else:
            artifact = next((item for item in artifacts if item["name"] == selected_source), None)
            if artifact:
                _render_artifact_file(artifact)
                if artifact["kind"] == "code":
                    st.caption(
                        "Code is shown for review and download. Running uploaded code is disabled "
                        "because this workspace does not provide an isolated execution sandbox."
                    )


def render_sessions_page() -> None:
    st.markdown("## Sessions")
    st.caption("Open a saved conversation from your private MongoDB chat history.")
    titles = history_titles()
    if not titles:
        st.info("No saved conversations yet.")
        return
    with st.container(border=True):
        st.markdown(f"**{len(titles)} saved conversations**")
        for index, title in enumerate(titles):
            columns = st.columns([1, 0.25], vertical_alignment="center")
            with columns[0]:
                st.write(title)
            with columns[1]:
                if st.button(
                    "Open",
                    key=f"open_session_{index}",
                    icon=":material/open_in_new:",
                    width="stretch",
                ):
                    load_chat(title)
                    st.session_state.nav = "New chat"
                    st.rerun()


def render_library_page() -> None:
    """File library: previously uploaded and generated files only."""
    st.markdown("## My Files")
    st.caption("Everything you uploaded to or generated inside chat. Open it again, or download it.")
    if st.button("Reload from server", key="library_reload", icon=":material/refresh:"):
        st.rerun()
    titles = history_titles()
    generated_total = 0
    if not titles:
        st.info("No saved conversations yet — upload a file in AI Chat to start your library.")
        return
    for index, title in enumerate(titles):
        files = load_chat_files(title)
        if not files:
            continue
        with st.container(border=True, key=f"library_chat_{index}"):
            st.markdown(f"**{escape(title)}**")
            for file_index, entry in enumerate(files):
                if entry.get("generated"):
                    generated_total += 1
                known = entry.get("data")
                file_name = entry.get("name", "file")
                file_columns = st.columns([1, 0.3], vertical_alignment="center")
                with file_columns[0]:
                    st.caption(f"{file_name} · {entry.get('kind', 'file')}")
                with file_columns[1]:
                    if isinstance(known, (bytes, bytearray)):
                        extension = file_name.rsplit(".", 1)[-1].lower()
                        st.download_button(
                            "Download",
                            data=bytes(known),
                            file_name=file_name,
                            mime=FILE_MIME_TYPES.get(extension, "application/octet-stream"),
                            key=f"library_dl_{index}_{file_index}",
                            use_container_width=True,
                        )
                    else:
                        st.caption("Preview only")
    st.caption(f"{generated_total} generated file(s) found in saved conversations.")


def load_chat_files(title: str) -> list[dict[str, Any]]:
    """Files (uploaded + generated) recorded for a saved conversation.

    Reads the saved record directly so browsing the library never swaps the
    conversation the user is currently in.
    """
    try:
        record = get_collection().find_one(
            {
                "$or": [
                    {"user_id": current_user_identifier()},
                    {"username": current_username()},
                    {"owner_identifier": current_username()},
                ],
                "title": title,
                "type": "chat",
            },
            sort=[("updated_at", -1)],
        )
    except Exception:
        return []
    if not record:
        return []
    messages = record.get("messages", []) or []
    files: list[dict[str, Any]] = []
    for message in messages:
        for name in message.get("files", []) or []:
            files.append({"name": str(name), "kind": "uploaded"})
        for generated in message.get("generated_files", []) or []:
            files.append(
                {
                    "name": generated.get("name", "file"),
                    "kind": generated.get("extension", "file"),
                    "data": generated.get("content"),
                    "generated": True,
                }
            )
    return files


def render_chat_workspace() -> None:
    with st.container(key="nexus-chat-workspace"):
        with st.container(key="nexus-chat-header"):
            header_columns = st.columns([1, 0.35], vertical_alignment="center")
            with header_columns[0]:
                st.markdown(f"### {escape(st.session_state.chat_title)}")
                st.caption("NEXUS AI · Gemini multimodal workspace")
            with header_columns[1]:
                if st.button(
                    "Canvas",
                    key="toggle_artifact_panel",
                    icon=":material/side_navigation:",
                    width="stretch",
                    help=(
                        "Hide the artifact canvas"
                        if st.session_state.artifact_panel_open
                        else "Show the artifact canvas"
                    ),
                ):
                    st.session_state.artifact_panel_open = not st.session_state.artifact_panel_open
                    st.rerun()

        if st.session_state.artifact_panel_open:
            chat_column, canvas_column = st.columns([1.2, 0.8], gap="medium")
        else:
            chat_column = st.container()
            canvas_column = None

        with chat_column:
            if st.session_state.get("chat_thread_start", 0) >= len(st.session_state.messages):
                render_chat_file_quick_actions()
            with st.container(height=360, key="nexus-chat-thread", border=False):
                previous_prompt = ""
                for message_index, message in enumerate(st.session_state.messages):
                    with st.chat_message(message["role"]):
                        if message.get("files"):
                            st.caption("📎 " + ", ".join(message["files"]))
                        if message.get("generated_files"):
                            for generated in message["generated_files"]:
                                render_generated_file_card(generated)
                        if message["role"] == "assistant":
                            st.markdown(message["content"])
                            render_assistant_actions(
                                message_index,
                                str(message["content"]),
                                previous_prompt,
                            )
                        else:
                            st.write(message["content"])
                            previous_prompt = str(message["content"])

            response_placeholder = st.empty()
            parsed: list[tuple[str, Any, str]] = []
            submitted_prompt: str | None = None
            send_voice = False
            send_files = False

            with st.container(key="unified_chat_bar", border=True):
                controls = st.columns([0.8, 1.35, 1.0], vertical_alignment="center")
                with controls[0]:
                    with st.popover("＋ Attach", width="stretch"):
                        uploaded = st.file_uploader(
                            "Add files",
                            type=SUPPORTED_FILES,
                            accept_multiple_files=True,
                            key=f"chat_files_{st.session_state.file_input_nonce}",
                        )
                with controls[1]:
                    audio = st.audio_input(
                        "Record voice",
                        key=f"chat_audio_{st.session_state.audio_input_nonce}",
                    )
                with controls[2]:
                    st.session_state.model = st.selectbox(
                        "Model",
                        ["Flash", "Pro"],
                        index=0 if st.session_state.model == "Flash" else 1,
                        label_visibility="collapsed",
                        key="chat_model",
                    )

                if uploaded:
                    artifact_files = []
                    artifact_cache = {
                        item.get("sha256"): item
                        for item in st.session_state.get("artifact_files", [])
                        if item.get("sha256")
                    }
                    for file in uploaded:
                        try:
                            file_data = file.getvalue()
                            file_digest = hashlib.sha256(file_data).hexdigest()
                            cached_artifact = artifact_cache.get(file_digest)
                            if cached_artifact and cached_artifact["name"] == file.name:
                                kind = cached_artifact["kind"]
                                content = cached_artifact["content"]
                            else:
                                kind, content = parse_file(file)
                            parsed.append((kind, content, file.name))
                            artifact_files.append(
                                {
                                    "name": file.name,
                                    "extension": file.name.rsplit(".", 1)[-1].lower(),
                                    "kind": kind,
                                    "content": content,
                                    "data": file_data,
                                    "sha256": file_digest,
                                }
                            )
                        except Exception as exc:
                            details = log_connection_failure(
                                exc,
                                endpoint="local file parsing",
                                payload={"filename": file.name, "size_bytes": len(file.getvalue())},
                            )
                            set_feedback(
                                "error",
                                "File could not be opened",
                                f"Auralis could not read {file.name}. Check the file format and try again.",
                                details=details,
                            )
                    st.session_state["artifact_files"] = artifact_files
                    if artifact_files and st.session_state.get("artifact_source") == "Latest response":
                        st.session_state["artifact_source"] = artifact_files[0]["name"]

                file_names = [name for _, _, name in parsed]
                audio_data = audio.getvalue() if audio is not None else None
                if file_names or audio_data:
                    preview_columns = st.columns([1, 0.3], vertical_alignment="center")
                    badges = [f"📎 {name}" for name in file_names]
                    if audio_data:
                        badges.append(f"🎙️ Voice · {len(audio_data) // 1024} KB")
                    with preview_columns[0]:
                        st.markdown(
                            "<div class='attachment-preview'>"
                            + " &nbsp;·&nbsp; ".join(escape(badge) for badge in badges)
                            + "</div>",
                            unsafe_allow_html=True,
                        )
                    with preview_columns[1]:
                        if st.button(
                            "Clear",
                            key="clear_chat_attachments",
                            icon=":material/close:",
                            help="Remove attached files and recording",
                            width="stretch",
                        ):
                            clear_chat_attachments()

                context_parts: list[str] = []
                images: list[Any] = []
                for kind, content, name in parsed:
                    if kind == "image":
                        images.append(content)
                    else:
                        document_text = _document_context(kind, content)
                        if kind == "code":
                            context_parts.append(f"{name}:\n```\n{document_text}\n```")
                        else:
                            context_parts.append(f"{kind.upper()} {name}:\n{document_text}")
                context = "\n\n".join(context_parts)

                if not effective_gemini_api_key():
                    st.caption(
                        "AI responses are ready when configured · Add `GEMINI_API_KEY` "
                        "to your environment or Streamlit secrets."
                    )

                submitted_prompt = st.chat_input("Message Auralis…", key="main_chat_input")
                if audio_data:
                    send_voice = st.button(
                        "Send voice",
                        key="send_voice",
                        icon=":material/mic:",
                        width="stretch",
                    )
                if parsed:
                    send_files = st.button(
                        "Analyze files",
                        key="send_files",
                        icon=":material/description:",
                        width="stretch",
                    )

        if canvas_column is not None:
            with canvas_column:
                render_artifact_panel()

        retry_prompt = st.session_state.pop("retry_prompt", None)
        if retry_prompt is not None:
            submitted_prompt = retry_prompt
        if submitted_prompt is None and not send_voice and not send_files:
            render_pending_feedback()
            return

        prompt = submitted_prompt or (
            "Please analyze and summarize the attached files."
            if send_files
            else "🎙️ Voice message"
        )
        st.session_state["last_context_prompt"] = prompt
        set_active_theme(st.session_state.get("nav", "New chat"), prompt)
        render_active_theme()
        live_context = ""
        live_sources: list[dict[str, str]] = []
        if not images and not audio_data and needs_live_web_search(prompt):
            try:
                with st.status("Searching the live web", expanded=False) as live_status:
                    live_context, live_sources = live_web_context(prompt)
                    live_status.update(
                        label=f"Live web context ready · {len(live_sources)} sources",
                        state="complete",
                    )
            except Exception as exc:
                log_connection_failure(
                    exc,
                    endpoint="DuckDuckGo HTML search",
                    payload={"query": prompt},
                    function="live_web_context",
                )
        prompt_context = "\n\n".join(part for part in (context, live_context) if part)
        full_prompt = f"{prompt_context}\n\nUser request: {prompt}" if prompt_context else prompt
        if live_sources:
            st.caption(
                "Live sources: "
                + " · ".join(f"[{index}]({source['url']})" for index, source in enumerate(live_sources, start=1))
            )
        streamed_chunks: list[str] = []

        def display_chunk(chunk: str) -> None:
            streamed_chunks.append(chunk)
            response_placeholder.markdown("".join(streamed_chunks))

        try:
            with processing_pulse(), st.spinner("Auralis is thinking…"):
                voice_payload = audio_data if send_voice or submitted_prompt is not None else None
                if not images and not voice_payload and openrouter_credentials():
                    response = openrouter_generate(
                        full_prompt,
                        system_prompt="You are Auralis, a helpful AI workspace assistant.",
                        on_chunk=display_chunk,
                    )
                else:
                    response = gemini_generate(
                        full_prompt,
                        images,
                        voice_payload,
                        audio.type if audio is not None else "audio/wav",
                        on_chunk=display_chunk,
                    )
        except Exception as exc:
            response_placeholder.empty()
            if is_gemini_quota_error(exc) and not images and not audio_data:
                try:
                    with processing_pulse(), st.spinner(
                        "Primary AI quota reached · trying backup text model…"
                    ):
                        response = generate_hf_text(full_prompt)
                    response = (
                        "The primary AI quota is temporarily reached; answered using the backup model.\n\n"
                        + response
                    )
                    st.session_state.pop("feedback_dialog", None)
                    st.toast(
                        "Gemini quota reached; your answer was completed with the backup model.",
                        icon="⚠️",
                        duration="long",
                    )
                except Exception as fallback_error:
                    set_feedback(
                        "error",
                        "Backup AI service unavailable",
                        "The primary request reached its quota and the backup Hugging Face service also failed.",
                        details=fallback_error,
                    )
                    response = (
                        "The AI quota or rate limit was reached, and the backup text model is unavailable. "
                        "Try again later or configure a Hugging Face `HF_TOKEN_1` / `HF_API_TOKEN`."
                    )
            else:
                if is_gemini_quota_error(exc):
                    set_feedback(
                        "error",
                        "Gemini quota reached",
                        "The multimodal request could not be completed because the Gemini quota or rate limit was reached.",
                        details=exc,
                    )
                response = describe_gemini_error(exc)
        save_chat_turn(
            prompt,
            response,
            file_names + (
                ["Voice recording"]
                if audio_data and (send_voice or submitted_prompt is not None)
                else []
            ),
            voice_response=bool(audio_data and (send_voice or submitted_prompt is not None)),
        )
        st.session_state.file_input_nonce += 1
        st.session_state.audio_input_nonce += 1
        st.rerun()


def main() -> None:
    init_state()
    if not st.session_state.get("user_id") or not st.session_state.get("username"):
        try:
            auth_token = browser_auth_session_token()
            restored_user = user_from_persistent_auth_session(auth_token) if auth_token else None
            if restored_user:
                start_user_session(restored_user, persist=False)
                st.session_state["auth_session_token"] = auth_token
            elif auth_token:
                clear_browser_auth_cookie()
        except Exception as exc:
            set_connection_failure(
                exc,
                endpoint="MongoDB Atlas / authentication",
                payload={"collection": "user_sessions", "operation": "restore_session"},
                title="Session could not be restored",
                message="Your sign-in session could not be verified. Please sign in again.",
            )
    if not st.session_state.get("user_id") or not st.session_state.get("username"):
        set_active_theme("Workspace")
        render_active_theme()
        render_auth_screen()
        render_pending_feedback()
        return
    if not st.session_state.get("auth_session_token"):
        try:
            auth_token = create_persistent_auth_session(st.session_state["user_id"])
            st.session_state["auth_session_token"] = auth_token
            set_browser_auth_cookie(auth_token)
        except Exception as exc:
            set_connection_failure(
                exc,
                endpoint="MongoDB Atlas / authentication",
                payload={"collection": "user_sessions", "operation": "persist_active_session"},
                title="Sign-in could not be remembered",
                message="You are still signed in, but this session may not survive a page refresh.",
            )
    render_sidebar()
    nav = st.session_state.get("nav", "New chat")
    prompt_context = st.session_state.get("last_context_prompt", "") if nav in {"New chat", "Library"} else ""
    set_active_theme(nav, prompt_context)
    render_active_theme()
    if nav != "New chat":
        render_brand_header()
    if st.session_state.nav == "Images":
        with st.container(key="nexus-image-workspace"):
            controls_column, preview_column = st.columns([1.2, 1], gap="large")
            with controls_column:
                render_image_generation(render_result=False)
            with preview_column:
                render_media_result()
        render_fullscreen_preview_if_open()
        render_pending_feedback()
        return
    if st.session_state.nav == "Videos":
        with st.container(key="nexus-video-workspace"):
            controls_column, preview_column = st.columns([1.2, 1], gap="large")
            with controls_column:
                render_video_generation(render_result=False)
            with preview_column:
                render_media_result()
                render_advanced_video_comparison()
        render_fullscreen_preview_if_open()
        render_pending_feedback()
        return
    # Legacy destinations keep working: they now point at the merged screens.
    if st.session_state.nav in {"Prompt to Video", "Image to Video"}:
        st.session_state.nav = "Videos"
        st.rerun()
    if st.session_state.nav == "Video Editing":
        with st.container(key="nexus-advanced-workspace"):
            controls_column, preview_column = st.columns([1.2, 1], gap="large")
            with controls_column:
                trim_tab, advanced_tab, pro_tab = st.tabs(
                    ["Trim and audio", "Advanced editing", "Professional editor"]
                )
                with trim_tab:
                    render_video_studio_editor()
                with advanced_tab:
                    render_advanced_video_studio()
                with pro_tab:
                    render_pro_video_studio()
            with preview_column:
                render_advanced_video_comparison()
                if not st.session_state.get("advanced_video_result"):
                    with st.container(key="nexus-advanced-empty-preview", border=True):
                        st.markdown("### Live Output Preview")
                        st.caption("Original and edited footage will appear here after processing.")
        render_fullscreen_preview_if_open()
        render_pending_feedback()
        return
    if st.session_state.nav == "Chat Rooms":
        render_chat_rooms()
        render_pending_feedback()
        return
    if st.session_state.nav == "Admin Dashboard":
        render_admin_dashboard()
        render_pending_feedback()
        return
    if st.session_state.nav == "Sessions":
        render_sessions_page()
        render_pending_feedback()
        return
    if st.session_state.nav == "Library":
        render_library_page()
        render_pending_feedback()
        return
    if st.session_state.nav == "Artifacts":
        render_artifact_panel()
        render_pending_feedback()
        return
    if st.session_state.nav in {"Settings", "Admin Panel", "Customize"}:
        render_role_settings()
        render_pending_feedback()
        return
    render_chat_workspace()
    render_fullscreen_preview_if_open()
    render_pending_feedback()


if __name__ == "__main__":
    main()
