"""Shared username and security-question validation for account recovery."""

from __future__ import annotations

import hmac
import re
import unicodedata


ADMIN_USERNAME = "admin"
SECURITY_QUESTIONS = (
    "What was the name of your first pet?",
    "What city were you born in?",
    "What is your favorite book?",
)
PASSWORD_RESET_MAX_ATTEMPTS = 5
PASSWORD_RESET_LOCK_MINUTES = 15


def normalize_username(username: str) -> str:
    return username.strip().casefold()


def validate_username(username: str) -> str:
    normalized = normalize_username(username)
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,31}", normalized):
        raise ValueError("Choose a username with 3–32 letters, numbers, periods, underscores, or hyphens.")
    return normalized


def is_admin_bootstrap_authorized(
    username: str,
    supplied_token: str,
    configured_token: str,
    admin_username: str = ADMIN_USERNAME,
) -> bool:
    return (
        normalize_username(username) == normalize_username(admin_username)
        and len(configured_token) >= 32
        and hmac.compare_digest(supplied_token, configured_token)
    )


def normalize_security_answer(answer: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", answer).casefold().split())
