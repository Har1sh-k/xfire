"""Local auth store and CLI credential readers for CrossFire agent authentication.

Priority chain for each agent:
  - Claude : ANTHROPIC_API_KEY env var → setup-token in auth store
  - Codex  : OPENAI_API_KEY env var → OPENAI_API_KEY in ~/.codex/auth.json
  - Gemini : GOOGLE_API_KEY env var → access_token in ~/.gemini/oauth_creds.json
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from pydantic import BaseModel, Field


class OAuthCredential(BaseModel):
    """OAuth credential record for a provider."""

    provider: str
    access_token: str
    refresh_token: str | None = None
    expires_at: int | None = None
    email: str | None = None
    account_id: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    project_id: str | None = None
    id_token: str | None = None
    api_key: str | None = None


class TokenCredential(BaseModel):
    """Opaque token credential record for providers like Claude setup-token."""

    provider: str
    token: str
    expires_at: int | None = None


class AuthStore(BaseModel):
    """Root auth store format persisted to .crossfire/auth.json."""

    version: int = 1
    oauth: dict[str, OAuthCredential] = Field(default_factory=dict)
    tokens: dict[str, TokenCredential] = Field(default_factory=dict)


def resolve_auth_path(repo_dir: str | None = None) -> Path:
    """Resolve auth storage path.

    Priority: CROSSFIRE_AUTH_PATH > <repo_dir>/.crossfire/auth.json > cwd/.crossfire/auth.json.
    """
    import os

    if os.environ.get("CROSSFIRE_AUTH_PATH"):
        return Path(os.environ["CROSSFIRE_AUTH_PATH"]).expanduser()
    if repo_dir:
        return Path(repo_dir) / ".crossfire" / "auth.json"
    return Path.cwd() / ".crossfire" / "auth.json"


def load_auth_store(auth_path: Path | None = None) -> AuthStore:
    """Load auth store or return empty defaults when missing/invalid."""
    path = auth_path or resolve_auth_path()
    if not path.exists():
        return AuthStore()

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return AuthStore()

    try:
        return AuthStore.model_validate(payload)
    except Exception:
        return AuthStore()


def save_auth_store(store: AuthStore, auth_path: Path | None = None) -> None:
    """Persist auth store to disk."""
    path = auth_path or resolve_auth_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(store.model_dump(), indent=2) + "\n", encoding="utf-8")


def _now_ms() -> int:
    return int(time.time() * 1000)


def _is_expired(expires_at: int | None, *, skew_s: int = 120) -> bool:
    if not expires_at:
        return False
    return (_now_ms() + skew_s * 1000) >= expires_at


# ---------------------------------------------------------------------------
# Claude — setup-token in auth store
# ---------------------------------------------------------------------------

def upsert_claude_setup_token(
    token: str,
    *,
    expires_at: int | None = None,
    auth_path: Path | None = None,
) -> None:
    """Store a Claude setup-token in the local auth store."""
    cleaned = token.strip()
    if not cleaned:
        raise ValueError("Token is required")

    store = load_auth_store(auth_path)
    store.tokens["claude"] = TokenCredential(
        provider="claude",
        token=cleaned,
        expires_at=expires_at,
    )
    save_auth_store(store, auth_path)


def get_claude_setup_token(auth_path: Path | None = None) -> str | None:
    """Fetch Claude setup-token from auth store if present and not expired."""
    store = load_auth_store(auth_path)
    token_cred = store.tokens.get("claude")
    if not token_cred:
        return None
    if _is_expired(token_cred.expires_at):
        return None
    return token_cred.token.strip() or None


# ---------------------------------------------------------------------------
# Claude — read OAuth token from ~/.claude/.credentials.json
# ---------------------------------------------------------------------------

def read_claude_cli_credentials() -> str | None:
    """Read the Claude Code CLI OAuth access token from ~/.claude/.credentials.json.

    The Claude Code CLI stores its OAuth credentials here after the first login.
    The ``accessToken`` field has scope ``user:inference`` which allows making
    inference calls.  We surface it so API mode can use it as a Bearer token
    via ``anthropic.AsyncAnthropic(auth_token=...)``.

    Returns the access token, or None if the file is absent / malformed / expired.
    """
    candidates = [
        Path.home() / ".claude" / ".credentials.json",
    ]

    for path in candidates:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        if not isinstance(data, dict):
            continue

        oauth = data.get("claudeAiOauth")
        if not isinstance(oauth, dict):
            continue

        access_token = oauth.get("accessToken")
        if not isinstance(access_token, str) or not access_token.strip():
            continue

        expires_at = oauth.get("expiresAt")
        if isinstance(expires_at, (int, float)) and _is_expired(int(expires_at)):
            continue

        return access_token.strip()

    return None


# ---------------------------------------------------------------------------
# Codex — read OPENAI_API_KEY from ~/.codex/auth.json
# ---------------------------------------------------------------------------

def read_codex_cli_credentials() -> str | None:
    """Read credentials that the Codex CLI stored in ~/.codex/auth.json.

    Priority:
    1. ``OPENAI_API_KEY`` top-level field (non-null string) — standard API key.
    2. ``tokens.access_token`` — OAuth access token from the Codex CLI login
       flow.  This can be used as a bearer token for subscription-based access.

    Returns the credential string, or None if the file is absent / malformed.
    """
    candidates = [
        Path.home() / ".codex" / "auth.json",
    ]

    for path in candidates:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        if not isinstance(data, dict):
            continue

        # Prefer a real API key if set
        api_key = data.get("OPENAI_API_KEY")
        if isinstance(api_key, str) and api_key.strip():
            return api_key.strip()

        # Fall back to OAuth access_token stored under tokens{}
        tokens = data.get("tokens")
        if isinstance(tokens, dict):
            access_token = tokens.get("access_token")
            if isinstance(access_token, str) and access_token.strip():
                return access_token.strip()

    return None


def get_codex_api_key(
    *,
    auth_path: Path | None = None,
    refresh_if_needed: bool = True,  # kept for API compat
) -> str | None:
    """Return a usable OpenAI API key.

    Sources tried in order:
    1. ``api_key`` stored in the CrossFire auth store (legacy / manual).
    2. ``OPENAI_API_KEY`` from the Codex CLI credential file (~/.codex/auth.json).
    """
    store = load_auth_store(auth_path)
    cred = store.oauth.get("codex")
    if cred and cred.api_key and cred.api_key.strip():
        return cred.api_key.strip()

    return read_codex_cli_credentials()


# ---------------------------------------------------------------------------
# Gemini — read OAuth token from ~/.gemini/oauth_creds.json
# ---------------------------------------------------------------------------

def read_gemini_cli_credentials() -> tuple[str, int | None] | None:
    """Read the Gemini CLI OAuth token from ``~/.gemini/oauth_creds.json``.

    The Gemini CLI stores OAuth credentials in this file after the first login.
    We read the ``access_token`` and ``expiry_date`` (milliseconds epoch) so
    the caller can decide whether to refresh.

    Returns ``(access_token, expiry_date_ms)`` or ``None`` if unavailable.
    """
    candidates = [
        Path.home() / ".gemini" / "oauth_creds.json",
        Path.home() / "AppData" / "Roaming" / "gemini" / "oauth_creds.json",
    ]

    for path in candidates:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        if not isinstance(data, dict):
            continue

        access_token = data.get("access_token")
        if not isinstance(access_token, str) or not access_token.strip():
            continue

        expiry = data.get("expiry_date")
        expiry_ms: int | None = int(expiry) if isinstance(expiry, (int, float)) else None

        return access_token.strip(), expiry_ms

    return None


def get_gemini_access_token(
    *,
    auth_path: Path | None = None,
    refresh_if_needed: bool = True,  # kept for API compat
) -> str | None:
    """Return a valid Gemini OAuth access token.

    Sources tried in order:
    1. CrossFire auth store OAuth credential (manually stored).
    2. Gemini CLI credential file (~/.gemini/oauth_creds.json).

    Note: token refresh is not attempted here — if the stored token is
    expired, the caller should prompt the user to re-run ``gemini`` CLI.
    """
    store = load_auth_store(auth_path)
    cred = store.oauth.get("gemini")
    if cred and cred.access_token.strip() and not _is_expired(cred.expires_at):
        return cred.access_token.strip()

    result = read_gemini_cli_credentials()
    if result is None:
        return None

    access_token, expiry_ms = result
    if _is_expired(expiry_ms):
        return None

    return access_token


def upsert_oauth_credential(
    provider: str,
    credential: OAuthCredential,
    *,
    auth_path: Path | None = None,
) -> None:
    """Store/update OAuth credentials by provider."""
    store = load_auth_store(auth_path)
    store.oauth[provider] = credential
    save_auth_store(store, auth_path)


# ---------------------------------------------------------------------------
# Status helpers
# ---------------------------------------------------------------------------

def has_credentials_for_agent(agent_name: str, auth_path: Path | None = None) -> bool:
    """Cheap check for whether usable credentials exist for an agent."""
    if agent_name == "claude":
        return bool(get_claude_setup_token(auth_path))

    if agent_name == "codex":
        return bool(get_codex_api_key(auth_path=auth_path, refresh_if_needed=False))

    if agent_name == "gemini":
        return bool(get_gemini_access_token(auth_path=auth_path, refresh_if_needed=False))

    return False


def auth_status_rows(auth_path: Path | None = None) -> list[dict[str, str]]:
    """Build status rows for CLI display."""
    rows: list[dict[str, str]] = []

    # Claude — setup-token only
    store = load_auth_store(auth_path)
    claude_token = store.tokens.get("claude")
    if claude_token and claude_token.token.strip() and not _is_expired(claude_token.expires_at):
        claude_status = "configured"
        claude_expires = (
            time.strftime("%Y-%m-%d", time.localtime(claude_token.expires_at / 1000))
            if claude_token.expires_at
            else "-"
        )
    else:
        claude_status = "missing"
        claude_expires = "-"

    rows.append(
        {
            "provider": "claude",
            "source": "setup-token",
            "status": claude_status,
            "expires": claude_expires,
        }
    )

    # Codex — prefer auth store, fall back to CLI file
    codex_key = get_codex_api_key(auth_path=auth_path, refresh_if_needed=False)
    codex_source = "missing"
    if codex_key:
        store_cred = store.oauth.get("codex")
        codex_source = "auth-store" if (store_cred and store_cred.api_key) else "codex-cli"

    rows.append(
        {
            "provider": "codex",
            "source": codex_source,
            "status": "configured" if codex_key else "missing",
            "expires": "-",
        }
    )

    # Gemini — prefer auth store, fall back to CLI file
    gemini_token = get_gemini_access_token(auth_path=auth_path, refresh_if_needed=False)
    gemini_source = "missing"
    gemini_expires = "-"
    if gemini_token:
        store_cred = store.oauth.get("gemini")
        if store_cred and store_cred.access_token.strip():
            gemini_source = "auth-store"
            if store_cred.expires_at:
                gemini_expires = time.strftime(
                    "%Y-%m-%d", time.localtime(store_cred.expires_at / 1000)
                )
        else:
            gemini_source = "gemini-cli"
            cli_result = read_gemini_cli_credentials()
            if cli_result:
                _, expiry_ms = cli_result
                if expiry_ms:
                    gemini_expires = time.strftime(
                        "%Y-%m-%d", time.localtime(expiry_ms / 1000)
                    )

    rows.append(
        {
            "provider": "gemini",
            "source": gemini_source,
            "status": "configured" if gemini_token else "missing",
            "expires": gemini_expires,
        }
    )

    return rows
