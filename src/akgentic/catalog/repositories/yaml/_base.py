"""Generic YAML-file-per-entry repository with lazy caching and duplicate detection.

Provides CRUD operations that persist entries as individual YAML files named by id,
with cross-file duplicate-id detection on load and automatic cache invalidation on
writes.
"""

from __future__ import annotations

import builtins
import logging
from pathlib import Path

import yaml
from pydantic import BaseModel

from akgentic.catalog.models.errors import CatalogValidationError, EntryNotFoundError

logger = logging.getLogger(__name__)

_list = builtins.list  # Alias: the repository's list() method shadows the built-in


class YamlRepositoryBase[T: BaseModel]:
    """Generic base providing YAML file loading, caching, duplicate detection, and CRUD.

    Subclasses must set ``_entry_type`` to the concrete Pydantic model class.
    """

    _entry_type: type[T]

    def __init__(self, catalog_dir: Path) -> None:
        """Initialize with a catalog directory to scan for YAML entry files.

        Args:
            catalog_dir: Root directory containing ``*.yaml`` files, one entry per file.
        """
        self._catalog_dir = catalog_dir
        self._entries: _list[T] | None = None

    # --- Loading & Caching ---

    def _load_all(self) -> _list[T]:
        """Scan catalog_dir for *.yaml files, validate entries, detect duplicates.

        Returns:
            All validated entries across every YAML file in catalog_dir.

        Raises:
            CatalogValidationError: If duplicate ids are detected across files.
        """
        entries: _list[T] = []
        seen_ids: dict[str, Path] = {}

        if not self._catalog_dir.exists():
            return entries

        logger.debug("scanning catalog directory %s", self._catalog_dir)

        for yaml_path in sorted(self._catalog_dir.glob("*.yaml")):
            raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
            if raw is None:
                logger.warning("empty YAML file skipped: %s", yaml_path.name)
                continue
            if not isinstance(raw, builtins.list):
                if not isinstance(raw, dict):
                    logger.warning(
                        "unexpected YAML structure in %s, expected list or dict",
                        yaml_path.name,
                    )
                raw = [raw]
            for item in raw:
                entry = self._entry_type.model_validate(item)
                entry_id: str = getattr(entry, "id")
                if entry_id in seen_ids:
                    raise CatalogValidationError(
                        [
                            f"Duplicate id '{entry_id}' found in "
                            f"'{seen_ids[entry_id]}' and '{yaml_path}'"
                        ]
                    )
                seen_ids[entry_id] = yaml_path
                entries.append(entry)
            logger.debug("loaded %s entries from %s", len(raw), yaml_path.name)

        return entries

    def _ensure_loaded(self) -> _list[T]:
        """Return cached entries, loading from disk on first access.

        Returns:
            Cached list of all entries.
        """
        if self._entries is None:
            self._entries = self._load_all()
        else:
            logger.debug("using cached entries (%s items)", len(self._entries))
        return self._entries

    def reload(self) -> None:
        """Force a re-scan from disk on next access."""
        self._entries = None
        logger.debug("cache invalidated, will reload on next access")

    # --- Read Operations ---

    def get(self, id: str) -> T | None:
        """Return entry by id, or None if not found.

        Args:
            id: The entry id to look up.

        Returns:
            The matching entry, or None if not found.
        """
        for entry in self._ensure_loaded():
            if getattr(entry, "id") == id:
                return entry
        return None

    def list(self) -> _list[T]:
        """Return all cached entries.

        Returns:
            Shallow copy of the cached entry list.
        """
        return _list(self._ensure_loaded())

    # --- Write Operations ---

    def create(self, entry: T) -> str:
        """Persist a new entry to a YAML file and invalidate cache.

        Args:
            entry: The entry to persist.

        Returns:
            The id of the created entry.

        Raises:
            CatalogValidationError: If an entry with the same id already exists.
        """
        entry_id: str = getattr(entry, "id")
        logger.debug("creating entry %s", entry_id)

        # Pre-check: reject duplicate IDs across all existing files
        existing = self._ensure_loaded()
        for e in existing:
            if getattr(e, "id") == entry_id:
                raise CatalogValidationError(
                    [f"Entry with id '{entry_id}' already exists in the catalog"]
                )

        file_path = self._catalog_dir / f"{entry_id}.yaml"
        self._catalog_dir.mkdir(parents=True, exist_ok=True)

        data: _list[dict[str, object]] = [entry.model_dump(mode="json")]
        if file_path.exists():
            file_data = yaml.safe_load(file_path.read_text(encoding="utf-8"))
            if file_data is None:
                file_data = []
            if not isinstance(file_data, builtins.list):
                file_data = [file_data]
            file_data.append(entry.model_dump(mode="json"))
            data = file_data

        file_path.write_text(
            yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        logger.debug("wrote entry %s to %s", entry_id, file_path.name)
        self._entries = None
        return entry_id

    def update(self, id: str, entry: T) -> None:
        """Find file containing id, update entry in place, invalidate cache.

        Args:
            id: The id of the entry to update.
            entry: The new entry data.

        Raises:
            EntryNotFoundError: If no entry with the given id exists.
        """
        logger.debug("updating entry %s", id)
        for yaml_path in sorted(self._catalog_dir.glob("*.yaml")):
            raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
            if raw is None:
                continue
            if not isinstance(raw, builtins.list):
                raw = [raw]
            for i, item in enumerate(raw):
                if item.get("id") == id:
                    raw[i] = entry.model_dump(mode="json")
                    yaml_path.write_text(
                        yaml.dump(
                            raw,
                            default_flow_style=False,
                            sort_keys=False,
                            allow_unicode=True,
                        ),
                        encoding="utf-8",
                    )
                    logger.debug("updated entry %s in %s", id, yaml_path.name)
                    self._entries = None
                    return
        raise EntryNotFoundError(f"Entry with id '{id}' not found")

    def delete(self, id: str) -> None:
        """Find file containing id, remove entry, delete file if empty, invalidate cache.

        Args:
            id: The id of the entry to delete.

        Raises:
            EntryNotFoundError: If no entry with the given id exists.
        """
        logger.debug("deleting entry %s", id)
        for yaml_path in sorted(self._catalog_dir.glob("*.yaml")):
            raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
            if raw is None:
                continue
            if not isinstance(raw, builtins.list):
                raw = [raw]
            for i, item in enumerate(raw):
                if item.get("id") == id:
                    raw.pop(i)
                    if raw:
                        yaml_path.write_text(
                            yaml.dump(
                                raw,
                                default_flow_style=False,
                                sort_keys=False,
                                allow_unicode=True,
                            ),
                            encoding="utf-8",
                        )
                    else:
                        yaml_path.unlink()
                    logger.debug("deleted entry %s from %s", id, yaml_path.name)
                    self._entries = None
                    return
        raise EntryNotFoundError(f"Entry with id '{id}' not found")
