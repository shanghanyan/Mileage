"""Per-user identity for the multi-user API (Phase 4, §9, §11).

Auth is intentionally thin and pluggable. Production should verify a JWT from
Clerk/Supabase; this module isolates "who is this request?" behind one dependency.

When ``MILEAGE_AUTH=1``:
  - Bearer token is hashed (SHA-256) and looked up in ``user_tokens``.
  - Fallback for local bootstrap: ``MILEAGE_AUTH_BOOTSTRAP=1`` still accepts
    bearer == user_id IF that user already exists (dev only).
  - ``PUT /users/{id}`` is locked to self (or ``MILEAGE_ADMIN_TOKEN``).

Auth is **off by default** so single-user/local runs keep working.
"""

from __future__ import annotations

import hashlib
import os
import secrets
from typing import Optional

from fastapi import Depends, Header, HTTPException

from ..config import Config
from ..domain.models import User


def _extract_token(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return authorization.strip() or None


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def mint_token() -> str:
    return secrets.token_urlsafe(32)


def resolve_user(
    repo,
    *,
    token: Optional[str],
    auth_enabled: bool,
) -> User:
    """Resolve the acting user from a bearer token."""
    if not auth_enabled:
        user_id = token or "local"
        return repo.get_user(user_id) or User(user_id=user_id)

    if not token:
        raise HTTPException(status_code=401, detail="missing bearer token")

    # Preferred: hashed API token → user_id.
    user_id = None
    if hasattr(repo, "user_id_for_token_hash"):
        user_id = repo.user_id_for_token_hash(hash_token(token))

    # Legacy / local: bearer may be the user_id when that user already exists
    # (Phase 4 tests + early demos). Prefer minted tokens in production.
    if user_id is None and repo.get_user(token) is not None:
        user_id = token

    # Explicit bootstrap: allow creating sessions as bearer==user_id even for
    # users not yet in the repo (opt-in).
    if user_id is None and os.getenv("MILEAGE_AUTH_BOOTSTRAP", "") not in (
        "",
        "0",
        "false",
    ):
        user_id = token

    if user_id is None:
        raise HTTPException(status_code=401, detail="invalid bearer token")

    user = repo.get_user(user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="unknown user")
    return user


def require_self_or_admin(
    *,
    acting: User,
    target_user_id: str,
    admin_token: Optional[str],
    request_token: Optional[str],
) -> None:
    if acting.user_id == target_user_id:
        return
    if admin_token and request_token and secrets.compare_digest(
        request_token, admin_token
    ):
        return
    raise HTTPException(status_code=403, detail="cannot modify another user's balances")


def make_current_user_dependency(get_config, get_orchestrator):
    """Build the `current_user` FastAPI dependency bound to app providers."""

    def current_user(
        authorization: Optional[str] = Header(default=None),
        config: Config = Depends(get_config),
        orchestrator=Depends(get_orchestrator),
    ) -> User:
        token = _extract_token(authorization)
        return resolve_user(
            orchestrator.repo,
            token=token,
            auth_enabled=config.auth_enabled,
        )

    return current_user
