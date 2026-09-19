"""Local-token authentication shared by the app entry point and route modules.

The token is a per-installation secret persisted under ``.system_generated``.
It gates every state-changing API so that only the local browser session (which
receives it once on first load) can mutate the question bank.
"""

from __future__ import annotations

import os
import secrets

from fastapi import Header, HTTPException

from mathbank.asset_security import harden_private_path, write_private_text_atomic
from mathbank.paths import SYSTEM_GENERATED_DIR


def load_or_create_local_token() -> str:
    """Read the persisted local token, creating one when missing or too short."""

    token_dir = str(SYSTEM_GENERATED_DIR)
    os.makedirs(token_dir, exist_ok=True)
    harden_private_path(token_dir, directory=True)
    token_file = os.path.join(token_dir, "local_token")
    if os.path.exists(token_file):
        try:
            harden_private_path(token_file)
            with open(token_file, "r", encoding="utf-8") as f:
                token = f.read().strip()
                if token and len(token) >= 16:
                    return token
        except Exception as e:
            print(f"[Security] Failed to read persistent token: {e}")

    token = secrets.token_hex(16)
    try:
        write_private_text_atomic(token_file, token)
    except Exception as e:
        print(f"[Security] Failed to write persistent token: {e}")
    return token


LOCAL_TOKEN = load_or_create_local_token()


def require_local_token(x_local_token: str = Header(None, alias="X-Local-Token")) -> str:
    """FastAPI dependency enforcing the persistent local token.

    Every mutating endpoint must declare this instead of repeating the
    comparison inline, so the failure mode stays identical everywhere.
    """

    if not x_local_token or not secrets.compare_digest(x_local_token, LOCAL_TOKEN):
        raise HTTPException(status_code=401, detail="Unauthorized")
    return x_local_token
