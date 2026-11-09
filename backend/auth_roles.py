"""Role checks shared by the Streamlit authentication flow."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def is_admin_account(user: Mapping[str, Any]) -> bool:
    """Return admin status only from trusted persisted account fields."""
    return user.get("is_admin") is True or str(user.get("role", "")).casefold() in {
        "admin",
        "administrator",
    }
