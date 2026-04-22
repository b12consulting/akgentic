"""Binary document reader -- MarkItDown-based extraction with two-pass LLM fallback."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Literal, Protocol

from pydantic import BaseModel, PrivateAttr

if TYPE_CHECKING:
    from openai import OpenAI

_MIME_MAP: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}


class MediaContent(BaseModel):
    """In-memory binary image content with MIME type.

    Plain ``BaseModel`` (NOT ``SerializableBaseModel``) — in-memory only,
    never wire-serialized.  No pydantic-ai imports — framework-agnostic by design.
    """

    data: bytes
    media_type: str


TEXT_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".txt",
        ".md",
        ".py",
        ".js",
        ".ts",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".csv",
        ".html",
        ".xml",
        ".rst",
        ".cfg",
        ".ini",
        ".log",
    }
)


class FileTypeReader(Protocol):
    """Protocol for file type readers that extract text from binary content."""

    extensions: frozenset[str]

    def extract_text(self, content: bytes, path: str) -> str:
        """Extract text content from binary file bytes.

        Args:
            content: Raw bytes of the file.
            path: Original file path (used for suffix detection).

        Returns:
            Extracted text as a string.
        """
        ...


class DocumentReader(BaseModel):
    """MarkItDown-based document reader, fully Pydantic-serializable.

    Pass 1: Extract text via ``MarkItDown()`` (no LLM).
    Pass 2 (optional): If Pass 1 yields fewer than 50 non-whitespace characters
    and ``llm_client="openai"`` is set, lazily constructs ``OpenAI()`` and retries.
    If both passes yield fewer than 50 non-whitespace characters, returns a
    placeholder comment.
    """

    extensions: ClassVar[frozenset[str]] = frozenset(
        {
            ".pdf",
            ".docx",
            ".xlsx",
            ".xls",
            ".pptx",
            ".msg",
            ".epub",
            ".jpg",
            ".jpeg",
            ".png",
            ".gif",
            ".bmp",
            ".webp",
        }
    )

    llm_client: Literal["openai"] | None = None
    llm_model: str = "gpt-4o"

    _openai_client: OpenAI | None = PrivateAttr(default=None)

    def _get_openai_client(self) -> OpenAI | None:
        """Lazily create and cache an OpenAI client.

        Returns None if ``llm_client`` is not set.
        """
        if self.llm_client is None:
            return None
        if self._openai_client is None:
            from openai import OpenAI as _OpenAI  # noqa: PLC0415

            self._openai_client = _OpenAI()
        return self._openai_client

    @staticmethod
    def _convert_via_tempfile(md: Any, content: bytes, suffix: str) -> str:
        """Write content to a temp file, convert via MarkItDown, and clean up.

        Uses ``delete=False`` to avoid Windows file-locking issues when
        MarkItDown re-opens the file by name.

        Args:
            md: A ``MarkItDown`` instance (plain or LLM-enabled).
            content: Raw bytes to write.
            suffix: File suffix for the temp file (e.g. ".pdf").

        Returns:
            Extracted text content, or empty string if None.
        """
        tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        try:
            tmp.write(content)
            tmp.flush()
            tmp.close()
            result = md.convert(tmp.name)
            return result.text_content or ""
        finally:
            os.unlink(tmp.name)

    def extract_text(self, content: bytes, path: str) -> str:
        """Extract text from binary file content using MarkItDown.

        Args:
            content: Raw bytes of the file.
            path: Original file path (used for suffix detection).

        Returns:
            Extracted Markdown text, or a placeholder comment if extraction
            yields no meaningful content.

        Raises:
            ImportError: If ``markitdown`` is not installed.
        """
        try:
            from markitdown import MarkItDown  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ImportError(
                'markitdown not installed. Run: pip install "akgentic-tool[docs]"'
            ) from exc

        suffix = Path(path).suffix or ".bin"

        # Pass 1: plain MarkItDown (no LLM)
        text = self._convert_via_tempfile(MarkItDown(), content, suffix)

        # Pass 2: LLM vision fallback if Pass 1 yielded insufficient content
        openai_client = self._get_openai_client()
        if len("".join(text.split())) < 50 and openai_client is not None:
            md_vision = MarkItDown(llm_client=openai_client, llm_model=self.llm_model)
            text = self._convert_via_tempfile(md_vision, content, suffix)

        if len("".join(text.split())) < 50:
            return "<!-- markitdown: no text extracted -->"

        return text
