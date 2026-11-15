"""Phase 1B: the deployment files actually install and run the FastAPI backend.

The audit found render.yaml building with the Streamlit requirements file while
``rootDir: backend``, so the API was deployed without fastapi/uvicorn at all. These
tests read the configuration files (no network, no Docker daemon required).
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _read(*parts: str) -> str:
    return REPO_ROOT.joinpath(*parts).read_text(encoding="utf-8")


def _requirement_names(text: str) -> set[str]:
    names: set[str] = set()
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        match = re.match(r"([A-Za-z0-9_.\-]+)(\[[^\]]*\])?", line)
        if match:
            names.add(match.group(1).lower().replace("_", "-"))
    return names


REQUIRED_BACKEND_PACKAGES = {
    "fastapi",
    "uvicorn",
    "pydantic",
    "pydantic-settings",
    "sqlalchemy",
    "python-jose",
    "passlib",
    "psycopg",
    "motor",
    "python-multipart",
}


def test_backend_requirements_contain_the_web_stack():
    names = _requirement_names(_read("backend", "requirements.txt"))
    missing = REQUIRED_BACKEND_PACKAGES - names
    assert not missing, f"backend/requirements.txt is missing {sorted(missing)}"


def test_root_requirements_include_the_backend_and_streamlit_once():
    text = _read("requirements.txt")
    assert "-r backend/requirements.txt" in text
    names = _requirement_names(text)
    assert "streamlit" in names
    # No duplicate pins between the two files.
    backend_names = _requirement_names(_read("backend", "requirements.txt"))
    assert not (names & backend_names)


def test_no_two_packages_pinned_to_conflicting_versions():
    combined = _read("requirements.txt") + "\n" + _read("backend", "requirements.txt")
    versions: dict[str, set[str]] = {}
    for raw in combined.splitlines():
        line = raw.split("#", 1)[0].strip()
        match = re.match(r"([A-Za-z0-9_.\-]+)==([0-9][^\s;]*)", line)
        if match:
            versions.setdefault(match.group(1).lower(), set()).add(match.group(2))
    conflicts = {name: pins for name, pins in versions.items() if len(pins) > 1}
    assert not conflicts, conflicts


def test_render_build_installs_the_backend_requirements():
    text = _read("render.yaml")
    assert "rootDir: backend" in text
    build = re.search(r"buildCommand:\s*(.+)", text).group(1)
    assert "requirements.txt" in build
    assert "../requirements.txt" not in build
    assert "pip install" in build
    # No requirement-file recitation in the command (that is what drifted).
    assert all(package not in build for package in REQUIRED_BACKEND_PACKAGES)


def test_render_start_command_and_proxy_trust():
    text = _read("render.yaml")
    start = re.search(r"startCommand:\s*(.+)", text).group(1)
    assert "uvicorn app.main:app" in start
    assert "--proxy-headers" in start
    # Never trust forwarding headers from an arbitrary caller.
    assert '--forwarded-allow-ips="*"' not in start and "--forwarded-allow-ips=\"*\"" not in start
    assert "healthCheckPath: /api/v1/health" in text
    assert "ENVIRONMENT" in text and 'value: "production"' in text


def test_render_storage_is_not_claimed_persistent_on_the_free_plan():
    text = _read("render.yaml")
    assert "plan: free" in text
    # No ATTACHMENT_STORAGE_DIR pointing at an unmounted path.
    assert "ATTACHMENT_STORAGE_DIR" not in re.sub(r"#[^\n]*", "", text).split("services:")[1]
    assert "free" in text.lower() and "ephemeral" in text.lower()


def test_dockerfile_installs_backend_requirements_and_mounts_storage():
    text = _read("backend", "Dockerfile")
    assert "COPY backend/requirements.txt" in text
    assert "pip install -r /app/backend/requirements.txt" in text
    assert '--forwarded-allow-ips' in text
    compose = _read("docker-compose.yml")
    assert "ATTACHMENT_STORAGE_DIR: /data/attachments" in compose
    assert "api-data:/data" in compose
    assert "ENVIRONMENT: production" in compose


# Documentation-only values that are not secrets (URLs, model ids, numbers,
# booleans, CIDRs). Everything else must be empty or a <placeholder>.
NON_SECRET_KEYS = {
    "MONGODB_DATABASE", "AUTH_MONGODB_DATABASE", "OPENROUTER_MODEL",
    "OPENROUTER_BASE_URL", "ENVIRONMENT", "TRUSTED_PROXY_CIDRS",
    "AI_GATEWAY_MODE", "GEMINI_MODEL", "HF_TEXT_API_URL", "HF_IMAGE_API_URL",
    "LIVEKIT_URL", "LTX_API_BASE_URL", "FAL_VIDEO_MODEL",
    "POLLINATIONS_IMAGE_MODEL", "POLLINATIONS_AUDIO_MODEL",
    "CLOUDFLARE_IMAGE_PRIMARY_MODEL", "CLOUDFLARE_IMAGE_FAST_MODEL",
    "CLOUDFLARE_RESET_TIMEZONE", "ADMIN_USERNAME", "PROJECT_NAME",
    "API_V1_STR", "VITE_API_BASE_URL", "PROJECT_NAME",
    # Public Hugging Face repository ids; credentials live in the *_TOKEN values.
    "HF_VIDEO_SPACE", "HF_TEXT_TO_VIDEO_SPACE", "HF_VIDEO_TO_VIDEO_SPACE",
    "MODEL_SPACE",
}


def test_example_environment_files_hold_no_real_looking_values():
    for parts in ((".env.example",), ("backend", ".env.example"), ("frontend", ".env.example")):
        text = _read(*parts)
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            key = key.strip()
            value = value.strip().strip("\"")
            if not value:
                continue
            if key in NON_SECRET_KEYS or key.isupper() and key.endswith(("_MODEL", "_URL", "_SECONDS", "_QUOTA", "_LIMIT", "_TIMEZONE", "_CIDRS")):
                continue
            # Explicit placeholder text is allowed for a secret key.
            if value.casefold().startswith(("replace-with-", "your-", "<")):
                continue
            # Numbers, booleans and placeholders are fine.
            if value.replace(".", "", 1).isdigit() or value.lower() in {"true", "false"}:
                continue
            assert value.startswith("<") or value.startswith("[") or value.startswith("/"), (
                f"{parts}: {key} looks like a real value: {value[:12]}..."
            )
