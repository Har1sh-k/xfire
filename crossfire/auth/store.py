"""Local auth store and OAuth helpers for subscription-based provider access."""

from __future__ import annotations

import base64
import hashlib
import json
import queue
import re
import secrets
import shutil
import threading
import time
import webbrowser
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from pydantic import BaseModel, Field

CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_AUTH_URL = "https://auth.openai.com/oauth/authorize"
CODEX_TOKEN_URL = "https://auth.openai.com/oauth/token"
CODEX_REDIRECT_URI = "http://127.0.0.1:1455/auth/callback"
CODEX_SCOPE = "openid profile email offline_access"

GEMINI_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GEMINI_TOKEN_URL = "https://oauth2.googleapis.com/token"
GEMINI_USERINFO_URL = "https://www.googleapis.com/oauth2/v1/userinfo?alt=json"
GEMINI_REDIRECT_URI = "http://localhost:8085/oauth2callback"
GEMINI_SCOPE = " ".join(
    [
        "https://www.googleapis.com/auth/cloud-platform",
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/userinfo.profile",
    ]
)

GEMINI_ENV_CLIENT_ID_KEYS = ["CROSSFIRE_GEMINI_OAUTH_CLIENT_ID", "GEMINI_CLI_OAUTH_CLIENT_ID"]
GEMINI_ENV_CLIENT_SECRET_KEYS = [
    "CROSSFIRE_GEMINI_OAUTH_CLIENT_SECRET",
    "GEMINI_CLI_OAUTH_CLIENT_SECRET",
]

PromptFn = Callable[[str], str]
NotifyFn = Callable[[str], None]


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
    env_path = Path(Path.home(), ".crossfire", "auth.json")
    configured = env_path
    raw = Path.cwd()

    import os

    if os.environ.get("CROSSFIRE_AUTH_PATH"):
        configured = Path(os.environ["CROSSFIRE_AUTH_PATH"]).expanduser()
    elif repo_dir:
        configured = Path(repo_dir) / ".crossfire" / "auth.json"
    else:
        configured = raw / ".crossfire" / "auth.json"
    return configured


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


def _base64url_no_padding(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def _decode_jwt_claims(token: str | None) -> dict[str, object]:
    if not token:
        return {}
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1]
    padding = "=" * ((4 - len(payload) % 4) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload + padding)
        data = json.loads(decoded.decode("utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _jwt_exp_ms(token: str | None) -> int | None:
    claims = _decode_jwt_claims(token)
    exp = claims.get("exp")
    if isinstance(exp, int):
        return exp * 1000
    if isinstance(exp, float):
        return int(exp * 1000)
    return None


def _extract_codex_account_id(claims: dict[str, object]) -> str | None:
    keys = (
        "chatgpt_account_id",
        "account_id",
        "accountId",
        "https://api.openai.com/account_id",
    )
    for key in keys:
        value = claims.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _extract_email(*claims_sets: dict[str, object]) -> str | None:
    for claims in claims_sets:
        value = claims.get("email")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _generate_pkce() -> tuple[str, str]:
    verifier = _base64url_no_padding(secrets.token_bytes(32))
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    challenge = _base64url_no_padding(digest)
    return verifier, challenge


def _wait_for_local_callback(
    *,
    host: str,
    port: int,
    callback_path: str,
    timeout_s: int,
) -> tuple[str, str | None]:
    result_queue: queue.Queue[tuple[str | None, str | None, str | None]] = queue.Queue(maxsize=1)

    class OAuthCallbackHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A003
            return

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path != callback_path:
                self.send_response(404)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"Not found")
                return

            params = parse_qs(parsed.query)
            code = (params.get("code") or [None])[0]
            state = (params.get("state") or [None])[0]
            error = (params.get("error") or [None])[0]

            ok = bool(code) and not error
            self.send_response(200 if ok else 400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()

            if ok:
                body = (
                    "<html><body><h2>Authentication complete</h2>"
                    "<p>You can return to CrossFire.</p></body></html>"
                )
            else:
                body = (
                    "<html><body><h2>Authentication failed</h2>"
                    "<p>Return to CrossFire and retry.</p></body></html>"
                )
            self.wfile.write(body.encode("utf-8"))

            try:
                result_queue.put_nowait((code, state, error))
            except queue.Full:
                pass

    server = ThreadingHTTPServer((host, port), OAuthCallbackHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        code, state, error = result_queue.get(timeout=timeout_s)
    except queue.Empty as exc:
        raise TimeoutError("Timed out waiting for OAuth callback") from exc
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1.0)

    if error:
        raise RuntimeError(f"OAuth callback returned error: {error}")
    if not code:
        raise RuntimeError("OAuth callback did not include an authorization code")

    return code, state


def _parse_manual_oauth_input(raw: str) -> tuple[str, str | None]:
    value = raw.strip()
    if not value:
        raise ValueError("Input is empty")

    try:
        parsed = urlparse(value)
    except Exception:
        return value, None

    if not parsed.scheme or not parsed.netloc:
        return value, None

    params = parse_qs(parsed.query)
    code = (params.get("code") or [None])[0]
    state = (params.get("state") or [None])[0]
    if not code:
        raise ValueError("Redirect URL missing 'code' parameter")

    return code, state


def _notify(notify_fn: NotifyFn | None, message: str) -> None:
    if notify_fn:
        notify_fn(message)
    else:
        print(message)


def _prompt(prompt_fn: PromptFn | None, message: str) -> str:
    if prompt_fn:
        return prompt_fn(message)
    return input(message)


def _codex_authorize_url(challenge: str, state: str) -> str:
    params = {
        "response_type": "code",
        "client_id": CODEX_CLIENT_ID,
        "redirect_uri": CODEX_REDIRECT_URI,
        "scope": CODEX_SCOPE,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
        "originator": "codex_cli_rs",
        "state": state,
    }
    return f"{CODEX_AUTH_URL}?{urlencode(params)}"


def _exchange_codex_tokens(code: str, verifier: str) -> dict[str, object]:
    payload = {
        "grant_type": "authorization_code",
        "client_id": CODEX_CLIENT_ID,
        "code_verifier": verifier,
        "code": code,
        "redirect_uri": CODEX_REDIRECT_URI,
    }
    response = httpx.post(CODEX_TOKEN_URL, data=payload, timeout=30)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError("Unexpected Codex OAuth response")
    return data


def _exchange_codex_api_key(id_token: str) -> str | None:
    payload = {
        "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
        "client_id": CODEX_CLIENT_ID,
        "requested_token": "openai-api-key",
        "subject_token": id_token,
        "subject_token_type": "urn:ietf:params:oauth:token-type:id_token",
    }
    response = httpx.post(CODEX_TOKEN_URL, data=payload, timeout=30)
    if not response.is_success:
        return None

    data = response.json()
    if not isinstance(data, dict):
        return None

    token = data.get("access_token")
    if isinstance(token, str) and token.strip():
        return token.strip()
    return None


def refresh_codex_oauth(credential: OAuthCredential) -> OAuthCredential:
    """Refresh a Codex OAuth credential."""
    if not credential.refresh_token:
        raise RuntimeError("Codex credential has no refresh_token")

    payload = {
        "client_id": credential.client_id or CODEX_CLIENT_ID,
        "grant_type": "refresh_token",
        "refresh_token": credential.refresh_token,
        "scope": "openid profile email",
    }
    response = httpx.post(CODEX_TOKEN_URL, json=payload, timeout=30)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError("Unexpected Codex refresh response")

    access_token = str(data.get("access_token") or "").strip()
    if not access_token:
        raise RuntimeError("Codex refresh returned no access_token")

    refresh_token = str(data.get("refresh_token") or credential.refresh_token).strip()
    id_token = data.get("id_token")
    id_token = str(id_token).strip() if isinstance(id_token, str) else credential.id_token

    access_claims = _decode_jwt_claims(access_token)
    id_claims = _decode_jwt_claims(id_token)

    expires_at = _jwt_exp_ms(access_token)
    expires_in = data.get("expires_in")
    if isinstance(expires_in, int) and expires_in > 0:
        expires_at = _now_ms() + expires_in * 1000 - 5 * 60 * 1000

    api_key = credential.api_key
    if id_token:
        exchanged = _exchange_codex_api_key(id_token)
        if exchanged:
            api_key = exchanged

    return credential.model_copy(
        update={
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": expires_at,
            "email": _extract_email(access_claims, id_claims) or credential.email,
            "account_id": _extract_codex_account_id(access_claims)
            or _extract_codex_account_id(id_claims)
            or credential.account_id,
            "id_token": id_token,
            "api_key": api_key,
            "client_id": credential.client_id or CODEX_CLIENT_ID,
        }
    )


def login_codex_oauth(
    *,
    is_remote: bool = False,
    open_browser: bool = True,
    timeout_s: int = 300,
    prompt_fn: PromptFn | None = None,
    notify_fn: NotifyFn | None = None,
) -> OAuthCredential:
    """Run Codex browser OAuth and return normalized credential payload."""
    verifier, challenge = _generate_pkce()
    expected_state = _base64url_no_padding(secrets.token_bytes(24))
    auth_url = _codex_authorize_url(challenge, expected_state)

    code: str | None = None
    received_state: str | None = None
    local_error: Exception | None = None

    if not is_remote:
        if open_browser:
            try:
                webbrowser.open(auth_url)
            except Exception:
                pass

        try:
            code, received_state = _wait_for_local_callback(
                host="127.0.0.1",
                port=1455,
                callback_path="/auth/callback",
                timeout_s=timeout_s,
            )
        except Exception as exc:
            local_error = exc

    if code is None:
        if local_error:
            _notify(notify_fn, f"Local callback failed ({local_error}). Switching to manual mode.")
        _notify(notify_fn, "Open this URL in your browser to authenticate:")
        _notify(notify_fn, auth_url)
        raw_input = _prompt(prompt_fn, "Paste redirect URL (or code): ")
        code, received_state = _parse_manual_oauth_input(raw_input)

    if received_state and received_state != expected_state:
        raise RuntimeError("Codex OAuth state mismatch")

    token_data = _exchange_codex_tokens(code, verifier)

    access_token = str(token_data.get("access_token") or "").strip()
    refresh_token = str(token_data.get("refresh_token") or "").strip() or None
    id_token = token_data.get("id_token")
    id_token = str(id_token).strip() if isinstance(id_token, str) else None

    if not access_token:
        raise RuntimeError("Codex OAuth returned no access_token")

    access_claims = _decode_jwt_claims(access_token)
    id_claims = _decode_jwt_claims(id_token)

    expires_at = _jwt_exp_ms(access_token)
    expires_in = token_data.get("expires_in")
    if isinstance(expires_in, int) and expires_in > 0:
        expires_at = _now_ms() + expires_in * 1000 - 5 * 60 * 1000

    api_key = _exchange_codex_api_key(id_token) if id_token else None

    return OAuthCredential(
        provider="codex",
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=expires_at,
        email=_extract_email(access_claims, id_claims),
        account_id=_extract_codex_account_id(access_claims)
        or _extract_codex_account_id(id_claims),
        client_id=CODEX_CLIENT_ID,
        id_token=id_token,
        api_key=api_key,
    )


def _resolve_env(keys: list[str]) -> str | None:
    import os

    for key in keys:
        value = os.environ.get(key, "").strip()
        if value:
            return value
    return None


def _extract_gemini_cli_client_config() -> tuple[str, str | None] | None:
    pattern_client_id = re.compile(r"(\d+-[a-z0-9]+\.apps\.googleusercontent\.com)")
    pattern_client_secret = re.compile(r"(GOCSPX-[A-Za-z0-9_-]+)")

    candidates: list[Path] = []

    gemini_bin = shutil.which("gemini")
    if gemini_bin:
        gemini_path = Path(gemini_bin)
        candidates.extend(
            [
                gemini_path.parent.parent
                / "node_modules"
                / "@google"
                / "gemini-cli-core"
                / "dist"
                / "src"
                / "code_assist"
                / "oauth2.js",
                gemini_path.parent.parent
                / "node_modules"
                / "@google"
                / "gemini-cli-core"
                / "dist"
                / "code_assist"
                / "oauth2.js",
            ]
        )

    home = Path.home()
    candidates.extend(
        [
            home
            / "AppData"
            / "Roaming"
            / "npm"
            / "node_modules"
            / "@google"
            / "gemini-cli-core"
            / "dist"
            / "src"
            / "code_assist"
            / "oauth2.js",
            home
            / ".npm-global"
            / "lib"
            / "node_modules"
            / "@google"
            / "gemini-cli-core"
            / "dist"
            / "src"
            / "code_assist"
            / "oauth2.js",
            Path("/usr/local/lib/node_modules/@google/gemini-cli-core/dist/src/code_assist/oauth2.js"),
            Path("/opt/homebrew/lib/node_modules/@google/gemini-cli-core/dist/src/code_assist/oauth2.js"),
        ]
    )

    for candidate in candidates:
        if not candidate.exists():
            continue
        try:
            content = candidate.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        id_match = pattern_client_id.search(content)
        if not id_match:
            continue
        secret_match = pattern_client_secret.search(content)
        return id_match.group(1), secret_match.group(1) if secret_match else None

    return None


def _resolve_gemini_oauth_client_config() -> tuple[str, str | None]:
    env_client_id = _resolve_env(GEMINI_ENV_CLIENT_ID_KEYS)
    env_client_secret = _resolve_env(GEMINI_ENV_CLIENT_SECRET_KEYS)
    if env_client_id:
        return env_client_id, env_client_secret

    extracted = _extract_gemini_cli_client_config()
    if extracted:
        return extracted

    raise RuntimeError(
        "Gemini OAuth client id not found. Set CROSSFIRE_GEMINI_OAUTH_CLIENT_ID and "
        "CROSSFIRE_GEMINI_OAUTH_CLIENT_SECRET (optional)."
    )


def _gemini_authorize_url(client_id: str, challenge: str, state: str) -> str:
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": GEMINI_REDIRECT_URI,
        "scope": GEMINI_SCOPE,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "access_type": "offline",
        "prompt": "consent",
    }
    return f"{GEMINI_AUTH_URL}?{urlencode(params)}"


def _exchange_gemini_tokens(
    *,
    client_id: str,
    client_secret: str | None,
    code: str,
    verifier: str,
) -> dict[str, object]:
    payload = {
        "client_id": client_id,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": GEMINI_REDIRECT_URI,
        "code_verifier": verifier,
    }
    if client_secret:
        payload["client_secret"] = client_secret

    response = httpx.post(GEMINI_TOKEN_URL, data=payload, timeout=30)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError("Unexpected Gemini OAuth response")
    return data


def _fetch_gemini_user_email(access_token: str) -> str | None:
    try:
        response = httpx.get(
            GEMINI_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=20,
        )
    except Exception:
        return None

    if not response.is_success:
        return None

    data = response.json()
    if not isinstance(data, dict):
        return None

    email = data.get("email")
    if isinstance(email, str) and email.strip():
        return email.strip()
    return None


def refresh_gemini_oauth(credential: OAuthCredential) -> OAuthCredential:
    """Refresh a Gemini OAuth credential."""
    if not credential.refresh_token:
        raise RuntimeError("Gemini credential has no refresh_token")

    client_id = credential.client_id
    if not client_id:
        client_id, fallback_secret = _resolve_gemini_oauth_client_config()
        if not credential.client_secret:
            credential = credential.model_copy(update={"client_secret": fallback_secret})

    payload = {
        "client_id": client_id,
        "refresh_token": credential.refresh_token,
        "grant_type": "refresh_token",
    }
    if credential.client_secret:
        payload["client_secret"] = credential.client_secret

    response = httpx.post(GEMINI_TOKEN_URL, data=payload, timeout=30)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError("Unexpected Gemini refresh response")

    access_token = str(data.get("access_token") or "").strip()
    if not access_token:
        raise RuntimeError("Gemini refresh returned no access_token")

    expires_at = _jwt_exp_ms(access_token)
    expires_in = data.get("expires_in")
    if isinstance(expires_in, int) and expires_in > 0:
        expires_at = _now_ms() + expires_in * 1000 - 5 * 60 * 1000

    maybe_refresh = data.get("refresh_token")
    refresh_token = credential.refresh_token
    if isinstance(maybe_refresh, str) and maybe_refresh.strip():
        refresh_token = maybe_refresh.strip()

    return credential.model_copy(
        update={
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": expires_at,
            "email": _fetch_gemini_user_email(access_token) or credential.email,
            "client_id": client_id,
        }
    )


def login_gemini_oauth(
    *,
    is_remote: bool = False,
    open_browser: bool = True,
    timeout_s: int = 300,
    prompt_fn: PromptFn | None = None,
    notify_fn: NotifyFn | None = None,
) -> OAuthCredential:
    """Run Gemini browser OAuth and return normalized credential payload."""
    client_id, client_secret = _resolve_gemini_oauth_client_config()
    verifier, challenge = _generate_pkce()
    expected_state = verifier
    auth_url = _gemini_authorize_url(client_id, challenge, expected_state)

    code: str | None = None
    received_state: str | None = None
    local_error: Exception | None = None

    if not is_remote:
        if open_browser:
            try:
                webbrowser.open(auth_url)
            except Exception:
                pass

        try:
            code, received_state = _wait_for_local_callback(
                host="localhost",
                port=8085,
                callback_path="/oauth2callback",
                timeout_s=timeout_s,
            )
        except Exception as exc:
            local_error = exc

    if code is None:
        if local_error:
            _notify(notify_fn, f"Local callback failed ({local_error}). Switching to manual mode.")
        _notify(notify_fn, "Open this URL in your browser to authenticate:")
        _notify(notify_fn, auth_url)
        raw_input = _prompt(prompt_fn, "Paste redirect URL (or code): ")
        code, received_state = _parse_manual_oauth_input(raw_input)

    if received_state and received_state != expected_state:
        raise RuntimeError("Gemini OAuth state mismatch")

    token_data = _exchange_gemini_tokens(
        client_id=client_id,
        client_secret=client_secret,
        code=code,
        verifier=verifier,
    )

    access_token = str(token_data.get("access_token") or "").strip()
    refresh_token = str(token_data.get("refresh_token") or "").strip() or None
    if not access_token:
        raise RuntimeError("Gemini OAuth returned no access_token")

    expires_at = _jwt_exp_ms(access_token)
    expires_in = token_data.get("expires_in")
    if isinstance(expires_in, int) and expires_in > 0:
        expires_at = _now_ms() + expires_in * 1000 - 5 * 60 * 1000

    return OAuthCredential(
        provider="gemini",
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=expires_at,
        email=_fetch_gemini_user_email(access_token),
        client_id=client_id,
        client_secret=client_secret,
    )


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


def get_claude_setup_token(auth_path: Path | None = None) -> str | None:
    """Fetch Claude setup-token if present and not expired."""
    store = load_auth_store(auth_path)
    token_cred = store.tokens.get("claude")
    if not token_cred:
        return None
    if _is_expired(token_cred.expires_at):
        return None
    return token_cred.token.strip() or None


def get_codex_api_key(
    *,
    auth_path: Path | None = None,
    refresh_if_needed: bool = True,
) -> str | None:
    """Fetch Codex API key from stored OAuth credentials.

    For subscription auth, Codex login can mint an OpenAI API key via token exchange.
    """
    store = load_auth_store(auth_path)
    cred = store.oauth.get("codex")
    if not cred:
        return None

    updated = False
    if refresh_if_needed and _is_expired(cred.expires_at):
        cred = refresh_codex_oauth(cred)
        store.oauth["codex"] = cred
        updated = True

    if not cred.api_key and cred.id_token:
        exchanged = _exchange_codex_api_key(cred.id_token)
        if exchanged:
            cred = cred.model_copy(update={"api_key": exchanged})
            store.oauth["codex"] = cred
            updated = True

    if updated:
        save_auth_store(store, auth_path)

    if cred.api_key and cred.api_key.strip():
        return cred.api_key.strip()

    return None


def get_gemini_access_token(
    *,
    auth_path: Path | None = None,
    refresh_if_needed: bool = True,
) -> str | None:
    """Fetch Gemini OAuth access token and refresh when needed."""
    store = load_auth_store(auth_path)
    cred = store.oauth.get("gemini")
    if not cred:
        return None

    updated = False
    if refresh_if_needed and _is_expired(cred.expires_at):
        cred = refresh_gemini_oauth(cred)
        store.oauth["gemini"] = cred
        updated = True

    if updated:
        save_auth_store(store, auth_path)

    if not cred.access_token.strip():
        return None
    return cred.access_token


def has_credentials_for_agent(agent_name: str, auth_path: Path | None = None) -> bool:
    """Cheap check for whether auth store has usable credentials for an agent."""
    store = load_auth_store(auth_path)

    if agent_name == "claude":
        token = store.tokens.get("claude")
        return bool(token and token.token.strip() and not _is_expired(token.expires_at))

    if agent_name == "codex":
        cred = store.oauth.get("codex")
        if not cred:
            return False
        if cred.api_key and cred.api_key.strip():
            return True
        if cred.refresh_token and cred.refresh_token.strip():
            return True
        return bool(cred.access_token.strip())

    if agent_name == "gemini":
        cred = store.oauth.get("gemini")
        if not cred:
            return False
        if cred.refresh_token and cred.refresh_token.strip():
            return True
        return bool(cred.access_token.strip()) and not _is_expired(cred.expires_at)

    return False


def auth_status_rows(auth_path: Path | None = None) -> list[dict[str, str]]:
    """Build status rows for CLI display."""
    store = load_auth_store(auth_path)
    rows: list[dict[str, str]] = []

    claude = store.tokens.get("claude")
    rows.append(
        {
            "provider": "claude",
            "mode": "setup-token",
            "status": "configured"
            if claude and claude.token.strip() and not _is_expired(claude.expires_at)
            else "missing",
            "email": "-",
            "expires": "-"
            if not claude or not claude.expires_at
            else time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(claude.expires_at / 1000)),
        }
    )

    for provider in ("codex", "gemini"):
        cred = store.oauth.get(provider)
        if not cred:
            rows.append(
                {
                    "provider": provider,
                    "mode": "oauth",
                    "status": "missing",
                    "email": "-",
                    "expires": "-",
                }
            )
            continue

        if _is_expired(cred.expires_at):
            status = "expired"
        else:
            status = "configured"

        expires = "-"
        if cred.expires_at:
            expires = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(cred.expires_at / 1000))

        rows.append(
            {
                "provider": provider,
                "mode": "oauth",
                "status": status,
                "email": cred.email or "-",
                "expires": expires,
            }
        )

    return rows
