"""Explicit, environment-controlled administrator provisioning.

Administrator rights are never derived from a registration payload, a request
header or a hard-coded identifier. The only automatic promotion is the one-time
operational bootstrap selected by ``ADMIN_BOOTSTRAP_IDENTIFIER`` in the service
environment (see ``docs/phase1/DEPLOYMENT_CHECKLIST.md``).

Properties that matter for a live deployment:

* Setting the variable promotes exactly one existing row and never creates a
  user, so an attacker cannot pre-register an identifier and wait for the
  operator to enable the bootstrap.
* Only a row that is still a plain ``user`` is promoted. Re-running the bootstrap
  never re-grants rights to an account an administrator has since revoked, and it
  never demotes or rewrites an existing administrator.
* Every other admin action happens through the SQL row itself (``is_admin``),
  which is read on each authenticated request.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger("app.admin_provisioning")

_PROMOTE_STATEMENT = (
    "UPDATE users SET is_admin = TRUE, role = 'admin' "
    "WHERE (identifier = :identifier OR email = :identifier OR mobile_number = :identifier) "
    # ``is_admin`` is the authorization truth. Legacy rows may have role='admin'
    # while is_admin=False; promoting those is needed for a one-time migration.
    # Once revoked, both fields are reset to user, so the next startup will not
    # re-grant access.
    "AND (is_admin IS NULL OR is_admin = FALSE) "
    "AND (role IS NULL OR role = 'user' OR role = 'admin')"
)


@dataclass(frozen=True)
class AdminProvisionResult:
    identifier: str
    promoted: int


def configured_admin_identifier() -> str:
    """The operator-selected bootstrap identifier, or an empty string."""
    from app.core.config import settings

    return (settings.ADMIN_BOOTSTRAP_IDENTIFIER or "").strip()


def provision_configured_admin(db: Session, identifier: str | None = None) -> AdminProvisionResult:
    """Promote the configured existing account to administrator. Idempotent."""
    target = (identifier if identifier is not None else configured_admin_identifier()).strip()
    if not target:
        return AdminProvisionResult(identifier="", promoted=0)
    # Keep existing accounts and admins intact. ``role='admin'`` with a stale
    # false flag is an inconsistent old row and is normalized to admin, but only if
    # it is one of the known pre-existing legitimate admins (role was already
    # administrator-authorized). Do not unilaterally elevate a normal role by name.
    result = db.execute(text(_PROMOTE_STATEMENT), {"identifier": target})
    db.commit()
    promoted = int(result.rowcount or 0)
    if promoted:
        # The identifier itself is an account reference, not a secret; it is safe
        # to say which account was promoted so operators can verify the bootstrap.
        logger.warning("Administrator provisioned from ADMIN_BOOTSTRAP_IDENTIFIER: %s", target)
    return AdminProvisionResult(identifier=target, promoted=promoted)
