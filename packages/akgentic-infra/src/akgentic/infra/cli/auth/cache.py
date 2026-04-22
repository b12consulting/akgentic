"""Per-profile OIDC token cache I/O.

Implements ADR-021 §Decision 2 — token cache at
``~/.akgentic/credentials/<profile>.json`` with schema
``{access_token, refresh_token, expires_at}``. Nothing else is stored; no
``scope``, no ``id_token``, no ``token_type`` (Story 21.3 Dev Notes).

Design decisions
----------------

* **File permissions (0600) / directory permissions (0700) are created
  directly, not fixed after the fact.** The file is opened via
  :func:`os.open` with mode ``0o600``; the directory is created via
  :meth:`pathlib.Path.mkdir` with ``mode=0o700`` and re-chmodded if the
  process umask masked restrictive bits. No ``open(..., "w")`` + ``os.chmod``
  sequences — those leave a race window where the file is briefly world-
  readable.
* **Atomic rewrite via ``os.replace``.** Saves write to ``<path>.tmp`` first
  (mode 0600), then :func:`os.replace` atomically renames onto the final
  path. Readers never see a half-written file.
* **Test seam: ``credentials_dir`` keyword argument.** Each I/O function
  accepts ``credentials_dir: Path | None = None``; tests pass a ``tmp_path``
  directory. Production callers leave it ``None`` and we resolve to
  ``~/.akgentic/credentials``. This is the documented seam — do NOT reach
  into ``os.environ["HOME"]`` in callers.
* **No ``dict[str, Any]`` in the public surface.** Serialization goes through
  :meth:`TokenCacheEntry.model_dump_json` and :meth:`TokenCacheEntry.model_validate_json`
  (Golden Rule #1).

Corrupt-cache handling
----------------------

:func:`load_token_cache` catches :class:`pydantic.ValidationError` and
re-raises as :class:`TokenCacheCorruptError` so raw Pydantic surface does not
leak to CLI callers. :class:`OidcTokenProvider` decides whether to delete a
corrupt cache.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, ValidationError

from akgentic.infra.cli.auth.oidc import OidcProtocolError

_DEFAULT_CREDENTIALS_DIR = Path.home() / ".akgentic" / "credentials"


class TokenCacheEntry(BaseModel):
    """On-disk shape of a per-profile token cache entry.

    Fields are intentionally minimal — ADR-021 locks the schema and Story
    21.3 does not extend it. ``expires_at`` is epoch seconds (UTC).
    """

    access_token: str
    refresh_token: str
    expires_at: int


class TokenCacheCorruptError(OidcProtocolError):
    """Raised when a cache file exists but fails Pydantic validation."""


def _credentials_dir(credentials_dir: Path | None = None) -> Path:
    """Resolve the credentials directory.

    Args:
        credentials_dir: Optional override; tests pass a ``tmp_path``-derived
            directory. ``None`` resolves to ``~/.akgentic/credentials``.
    """
    return credentials_dir if credentials_dir is not None else _DEFAULT_CREDENTIALS_DIR


def _cache_path(profile_name: str, credentials_dir: Path | None) -> Path:
    return _credentials_dir(credentials_dir) / f"{profile_name}.json"


def _ensure_restrictive_dir(directory: Path) -> None:
    """Create ``directory`` with mode 0700; re-chmod if umask stripped bits.

    This runs before any cache-file write, so the parent dir is always
    restrictive before the file lands inside it. If the directory already
    exists we also force its mode back to 0700 (operators may fix permissions
    manually; we keep the invariant locally).
    """
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    # umask can mask restrictive bits during mkdir — fix explicitly.
    os.chmod(directory, 0o700)


def load_token_cache(
    profile_name: str,
    *,
    credentials_dir: Path | None = None,
) -> TokenCacheEntry | None:
    """Load the per-profile cache entry.

    Returns:
        A :class:`TokenCacheEntry` if the file exists and parses cleanly;
        ``None`` if the file is missing.

    Raises:
        TokenCacheCorruptError: The file exists but fails Pydantic validation
            (malformed JSON, missing fields, wrong types). The caller decides
            whether to delete it — this function does not self-purge.
    """
    path = _cache_path(profile_name, credentials_dir)
    if not path.exists():
        return None

    try:
        raw = path.read_bytes()
    except OSError as exc:  # pragma: no cover - defensive
        raise TokenCacheCorruptError(f"Cannot read cache file {path}: {exc}") from exc

    try:
        return TokenCacheEntry.model_validate_json(raw)
    except ValidationError as exc:
        raise TokenCacheCorruptError(
            f"Cache file {path} is corrupt or schema-incompatible: {exc}"
        ) from exc


def save_token_cache(
    profile_name: str,
    entry: TokenCacheEntry,
    *,
    credentials_dir: Path | None = None,
) -> None:
    """Atomically write ``entry`` to the per-profile cache file.

    Guarantees:

    * The parent directory exists with mode 0700 before the file is written.
    * The file is created with mode 0600 directly (no chmod-after-open race).
    * The write is atomic via ``os.replace`` on a ``.tmp`` sibling — readers
      never observe a truncated file.
    """
    directory = _credentials_dir(credentials_dir)
    _ensure_restrictive_dir(directory)

    final_path = directory / f"{profile_name}.json"
    tmp_path = directory / f"{profile_name}.json.tmp"

    payload = entry.model_dump_json().encode("utf-8")

    # Create the tmp file with the restrictive mode directly — no race.
    fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
    except BaseException:  # pragma: no cover - defensive cleanup
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise

    # Atomic replace — final_path inherits tmp's mode 0600.
    os.replace(str(tmp_path), str(final_path))
    # Re-assert mode on final path (paranoid: NFS or odd filesystems).
    os.chmod(final_path, 0o600)


def delete_token_cache(
    profile_name: str,
    *,
    credentials_dir: Path | None = None,
) -> None:
    """Remove the per-profile cache file if present. Idempotent."""
    path = _cache_path(profile_name, credentials_dir)
    # missing_ok handles both "never existed" and "already removed".
    path.unlink(missing_ok=True)


__all__ = [
    "TokenCacheCorruptError",
    "TokenCacheEntry",
    "delete_token_cache",
    "load_token_cache",
    "save_token_cache",
]
