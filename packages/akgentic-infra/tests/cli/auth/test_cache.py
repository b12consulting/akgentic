"""Tests for :mod:`akgentic.infra.cli.auth.cache` (Story 21.3).

All tests use ``tmp_path`` via the ``credentials_dir`` seam — never touch
real ``~/.akgentic/``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from akgentic.infra.cli.auth.cache import (
    TokenCacheCorruptError,
    TokenCacheEntry,
    delete_token_cache,
    load_token_cache,
    save_token_cache,
)


@pytest.fixture
def tmp_credentials_dir(tmp_path: Path) -> Path:
    """A per-test credentials directory isolated under ``tmp_path``."""
    return tmp_path / "credentials"


def _make_entry(expires_at: int = 1_700_000_000) -> TokenCacheEntry:
    return TokenCacheEntry(
        access_token="access-abc",
        refresh_token="refresh-xyz",
        expires_at=expires_at,
    )


def test_load_returns_none_when_file_missing(tmp_credentials_dir: Path) -> None:
    # AC #4: load returns None, never raises, on missing file.
    assert load_token_cache("profile-x", credentials_dir=tmp_credentials_dir) is None


def test_save_then_load_roundtrip(tmp_credentials_dir: Path) -> None:
    original = _make_entry()
    save_token_cache("profile-x", original, credentials_dir=tmp_credentials_dir)

    loaded = load_token_cache("profile-x", credentials_dir=tmp_credentials_dir)
    assert loaded is not None
    assert loaded.access_token == original.access_token
    assert loaded.refresh_token == original.refresh_token
    assert loaded.expires_at == original.expires_at


def test_save_creates_file_with_mode_0600(tmp_credentials_dir: Path) -> None:
    # AC #3: file permission enforcement (behavioral assertion via stat).
    save_token_cache("profile-x", _make_entry(), credentials_dir=tmp_credentials_dir)
    path = tmp_credentials_dir / "profile-x.json"
    assert path.exists()
    mode = path.stat().st_mode & 0o777
    assert mode == 0o600, f"expected 0600, got {oct(mode)}"


def test_save_rewrite_keeps_mode_0600(tmp_credentials_dir: Path) -> None:
    # On refresh we rewrite the same path — mode must remain 0600.
    save_token_cache("profile-x", _make_entry(), credentials_dir=tmp_credentials_dir)
    save_token_cache(
        "profile-x",
        _make_entry(expires_at=1_800_000_000),
        credentials_dir=tmp_credentials_dir,
    )
    path = tmp_credentials_dir / "profile-x.json"
    mode = path.stat().st_mode & 0o777
    assert mode == 0o600, f"expected 0600 after rewrite, got {oct(mode)}"


def test_save_creates_directory_with_mode_0700(tmp_credentials_dir: Path) -> None:
    # AC #3: directory permission enforcement.
    assert not tmp_credentials_dir.exists()
    save_token_cache("profile-x", _make_entry(), credentials_dir=tmp_credentials_dir)
    assert tmp_credentials_dir.is_dir()
    mode = tmp_credentials_dir.stat().st_mode & 0o777
    assert mode == 0o700, f"expected 0700, got {oct(mode)}"


def test_save_subsequent_save_keeps_directory_mode_0700(tmp_credentials_dir: Path) -> None:
    save_token_cache("profile-a", _make_entry(), credentials_dir=tmp_credentials_dir)
    save_token_cache("profile-b", _make_entry(), credentials_dir=tmp_credentials_dir)
    mode = tmp_credentials_dir.stat().st_mode & 0o777
    assert mode == 0o700


def test_load_raises_typed_error_on_malformed_json(tmp_credentials_dir: Path) -> None:
    # AC #7: corrupt cache surfaces typed error, not raw ValidationError.
    tmp_credentials_dir.mkdir(mode=0o700)
    (tmp_credentials_dir / "profile-x.json").write_text("{not valid json")

    with pytest.raises(TokenCacheCorruptError):
        load_token_cache("profile-x", credentials_dir=tmp_credentials_dir)


def test_load_raises_typed_error_on_missing_fields(tmp_credentials_dir: Path) -> None:
    tmp_credentials_dir.mkdir(mode=0o700)
    (tmp_credentials_dir / "profile-x.json").write_text(
        json.dumps({"access_token": "abc"})  # missing refresh_token + expires_at
    )

    with pytest.raises(TokenCacheCorruptError):
        load_token_cache("profile-x", credentials_dir=tmp_credentials_dir)


def test_delete_is_idempotent_when_absent(tmp_credentials_dir: Path) -> None:
    # AC #7: second call must not raise.
    delete_token_cache("profile-x", credentials_dir=tmp_credentials_dir)
    delete_token_cache("profile-x", credentials_dir=tmp_credentials_dir)


def test_delete_removes_existing_file(tmp_credentials_dir: Path) -> None:
    save_token_cache("profile-x", _make_entry(), credentials_dir=tmp_credentials_dir)
    path = tmp_credentials_dir / "profile-x.json"
    assert path.exists()

    delete_token_cache("profile-x", credentials_dir=tmp_credentials_dir)
    assert not path.exists()


def test_save_uses_model_dump_json_not_dict(tmp_credentials_dir: Path) -> None:
    # Golden Rule #1: persisted payload must be Pydantic-serialized JSON.
    save_token_cache("profile-x", _make_entry(), credentials_dir=tmp_credentials_dir)
    raw = (tmp_credentials_dir / "profile-x.json").read_text()
    # The shape must match TokenCacheEntry fields exactly — no extras, no missing.
    parsed = json.loads(raw)
    assert set(parsed.keys()) == {"access_token", "refresh_token", "expires_at"}
