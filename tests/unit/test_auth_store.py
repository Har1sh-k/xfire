"""Tests for auth store helpers."""

import json
from pathlib import Path

from xfire.auth.store import (
    OAuthCredential,
    auth_status_rows,
    get_claude_setup_token,
    get_codex_api_key,
    get_gemini_access_token,
    load_auth_store,
    save_auth_store,
    upsert_claude_setup_token,
    upsert_oauth_credential,
)


def test_claude_setup_token_roundtrip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    upsert_claude_setup_token("test-setup-token")
    assert get_claude_setup_token() == "test-setup-token"

    store = load_auth_store()
    assert store.tokens["claude"].provider == "claude"


def test_oauth_store_roundtrip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    cred = OAuthCredential(
        provider="gemini",
        access_token="access",
        refresh_token="refresh",
        expires_at=4102444800000,
        email="user@example.com",
    )
    upsert_oauth_credential("gemini", cred)

    token = get_gemini_access_token(refresh_if_needed=False)
    assert token == "access"


def test_codex_api_key_from_store(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    cred = OAuthCredential(
        provider="codex",
        access_token="access",
        refresh_token="refresh",
        expires_at=4102444800000,
        api_key="sk-test",
    )
    upsert_oauth_credential("codex", cred)

    assert get_codex_api_key(refresh_if_needed=False) == "sk-test"


def test_auth_status_rows_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    rows = auth_status_rows()
    providers = {row["provider"] for row in rows}
    assert providers == {"claude", "codex", "gemini"}


def test_auth_store_file_written(tmp_path):
    store_path = Path(tmp_path) / ".xfire" / "auth.json"
    store = load_auth_store(store_path)
    assert store.version == 1

    upsert = OAuthCredential(provider="codex", access_token="a")
    store.oauth["codex"] = upsert
    save_auth_store(store, store_path)

    payload = json.loads(store_path.read_text(encoding="utf-8"))
    assert payload["oauth"]["codex"]["access_token"] == "a"
