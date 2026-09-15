"""Admin password, session cookies, and runtime Anthropic settings (DATA_DIR)."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Any

from src.host.config import get_settings

log = logging.getLogger("mcp_workbench.settings")

COOKIE_NAME = "wb_admin"
SESSION_TTL_S = 60 * 60 * 24 * 7  # 7 days
PBKDF2_ITERATIONS = 260_000
EFFORT_LEVELS = frozenset({"low", "medium", "high"})


def _data_root() -> Path:
    root = get_settings().data_root()
    root.mkdir(parents=True, exist_ok=True)
    return root


def admin_path() -> Path:
    return _data_root() / "admin.json"


def llm_settings_path() -> Path:
    return _data_root() / "llm_settings.json"


def session_secret_path() -> Path:
    return _data_root() / "session_secret"


def get_session_secret() -> str:
    settings = get_settings()
    env = (settings.settings_session_secret or "").strip()
    if env:
        return env
    path = session_secret_path()
    if path.is_file():
        raw = path.read_text(encoding="utf-8").strip()
        if raw:
            return raw
    secret = secrets.token_urlsafe(32)
    path.write_text(secret + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return secret


def password_configured() -> bool:
    path = admin_path()
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool(data.get("salt") and data.get("hash"))


def hash_password(password: str, *, salt: bytes | None = None) -> dict[str, str]:
    if salt is None:
        salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PBKDF2_ITERATIONS,
    )
    return {
        "salt": salt.hex(),
        "hash": digest.hex(),
        "iterations": str(PBKDF2_ITERATIONS),
        "algo": "pbkdf2_hmac_sha256",
    }


def verify_password(password: str) -> bool:
    path = admin_path()
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    try:
        salt = bytes.fromhex(data["salt"])
        expected = bytes.fromhex(data["hash"])
        iterations = int(data.get("iterations") or PBKDF2_ITERATIONS)
    except (KeyError, ValueError):
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
    )
    return hmac.compare_digest(digest, expected)


def save_admin_password(password: str) -> None:
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters.")
    payload = hash_password(password)
    path = admin_path()
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def create_session_token(*, now: float | None = None) -> str:
    ts = int(now if now is not None else time.time())
    body = f"admin:{ts}"
    sig = hmac.new(
        get_session_secret().encode("utf-8"),
        body.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{body}.{sig}"


def verify_session_token(token: str | None, *, now: float | None = None) -> bool:
    if not token or "." not in token:
        return False
    body, _, sig = token.rpartition(".")
    if not body.startswith("admin:"):
        return False
    expected = hmac.new(
        get_session_secret().encode("utf-8"),
        body.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return False
    try:
        ts = int(body.split(":", 1)[1])
    except ValueError:
        return False
    current = now if now is not None else time.time()
    if current - ts > SESSION_TTL_S or ts > current + 60:
        return False
    return True


def load_llm_settings_file() -> dict[str, Any]:
    path = llm_settings_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_llm_settings(
    *,
    api_key: str | None = None,
    model: str | None = None,
    effort: str | None = None,
) -> dict[str, Any]:
    """Merge into llm_settings.json. Empty api_key keeps existing."""
    current = load_llm_settings_file()
    if api_key is not None and api_key.strip():
        current["anthropic_api_key"] = api_key.strip()
    if model is not None and model.strip():
        current["anthropic_model"] = model.strip()
    if effort is not None:
        effort_n = effort.strip().lower()
        if effort_n not in EFFORT_LEVELS:
            raise ValueError("effort must be low, medium, or high")
        current["anthropic_effort"] = effort_n
    # Ensure keys exist for a predictable file shape when saving from UI.
    if "anthropic_model" not in current:
        current["anthropic_model"] = get_settings().anthropic_model
    if "anthropic_effort" not in current:
        current["anthropic_effort"] = getattr(get_settings(), "anthropic_effort", "medium") or "medium"
    if "anthropic_api_key" not in current:
        current["anthropic_api_key"] = ""
    path = llm_settings_path()
    path.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return current


def key_suffix(key: str | None) -> str | None:
    if not key:
        return None
    key = key.strip()
    if len(key) < 4:
        return key
    return key[-4:]


def get_effective_llm() -> dict[str, Any]:
    """File overrides env/settings defaults. Reloaded on every call (no restart)."""
    settings = get_settings()
    file_data = load_llm_settings_file()
    api_key = (file_data.get("anthropic_api_key") or "").strip() or (settings.anthropic_api_key or "").strip()
    model = (file_data.get("anthropic_model") or "").strip() or (settings.anthropic_model or "claude-sonnet-4-20250514")
    effort = (file_data.get("anthropic_effort") or "").strip().lower() or (
        getattr(settings, "anthropic_effort", None) or "medium"
    )
    if effort not in EFFORT_LEVELS:
        effort = "medium"
    return {
        "anthropic_api_key": api_key,
        "anthropic_model": model,
        "anthropic_effort": effort,
        "configured": bool(api_key),
        "key_suffix": key_suffix(api_key),
    }


def public_llm_status() -> dict[str, Any]:
    eff = get_effective_llm()
    return {
        "llm_configured": eff["configured"],
        "model": eff["anthropic_model"],
        "effort": eff["anthropic_effort"],
        "key_suffix": eff["key_suffix"],
        "configured": eff["configured"],
    }
