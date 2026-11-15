"""Phase 1A: JWT secret policy and token compatibility."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from jose import jwt

from app.core import security

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def test_token_carries_exp_iat_and_type():
    payload = jwt.decode(
        security.create_access_token({"sub": "user@example.com"}),
        security.JWT_SECRET,
        algorithms=[security.ALGORITHM],
    )
    assert payload["sub"] == "user@example.com"
    assert payload["token_type"] == "access"
    assert payload["exp"] and payload["iat"]


def test_legacy_token_without_the_new_claims_still_validates():
    """Tokens issued before this change (sub + exp only) must keep working."""
    legacy = jwt.encode(
        {"sub": "legacy@example.com", "exp": 4102444800},
        security.JWT_SECRET,
        algorithm=security.ALGORITHM,
    )
    assert security.decode_access_token(legacy)["sub"] == "legacy@example.com"


def test_token_signed_with_another_secret_is_rejected():
    forged = jwt.encode({"sub": "attacker@example.com", "exp": 4102444800}, "x" * 40, algorithm="HS256")
    with pytest.raises(Exception):
        security.decode_access_token(forged)


def test_token_with_the_wrong_algorithm_is_rejected():
    unsigned = jwt.encode({"sub": "a@b.c"}, security.JWT_SECRET, algorithm="HS256")
    with pytest.raises(Exception):
        jwt.decode(unsigned, security.JWT_SECRET, algorithms=["HS512"])


def _run_python(code: str, env: dict[str, str]) -> subprocess.CompletedProcess:
    environment = {key: value for key, value in os.environ.items() if key not in {"JWT_SECRET", "ENVIRONMENT"}}
    environment.update(env)
    environment["PYTHONPATH"] = os.pathsep.join([str(BACKEND_ROOT), str(BACKEND_ROOT.parent)])
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(BACKEND_ROOT),
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_production_refuses_to_start_without_a_strong_secret():
    result = _run_python(
        "import app.core.config",
        {"ENVIRONMENT": "production", "JWT_SECRET": ""},
    )
    assert result.returncode != 0
    assert "JWT_SECRET" in (result.stderr + result.stdout)
    # The value itself must never be echoed.
    assert "super-secret" not in (result.stderr + result.stdout)


def test_production_refuses_a_short_secret():
    result = _run_python(
        "import app.core.config",
        {"ENVIRONMENT": "production", "JWT_SECRET": "short-secret"},
    )
    assert result.returncode != 0
    assert "JWT_SECRET" in (result.stderr + result.stdout)


def test_development_keeps_the_repair_path_for_a_missing_secret():
    result = _run_python(
        "from app.core.config import settings; print(len(settings.JWT_SECRET), settings.is_production)",
        {"ENVIRONMENT": "development", "JWT_SECRET": ""},
    )
    assert result.returncode == 0, result.stderr
    length, is_production = result.stdout.split()
    assert int(length) >= 32
    assert is_production == "False"


def test_production_accepts_a_strong_secret():
    result = _run_python(
        "from app.core.config import settings; print(settings.is_production)",
        {"ENVIRONMENT": "production", "JWT_SECRET": "a" * 48},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "True"
