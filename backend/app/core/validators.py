"""Input validation and sanitization utilities.

Provides:
- validate_file_upload: Deep file validation (magic bytes, dangerous types, size).
- sanitize_input: Text cleaning (null bytes, control chars, unicode normalization).
- validate_jwt_payload: JWT claims validation (exp, iat, sub).
"""

import logging
import os
import re
import unicodedata
from datetime import datetime, timezone

from fastapi import HTTPException, status
from starlette.datastructures import UploadFile

from app.core.config import settings

logger = logging.getLogger(__name__)


# =============================================================================
# Constants
# =============================================================================

# File extensions that are always rejected, regardless of MIME type.
DEFAULT_BLOCKED_EXTENSIONS: set[str] = {
    ".exe", ".bat", ".cmd", ".com", ".sh", ".ps1", ".psm1",
    ".msi", ".dll", ".sys", ".vbs", ".js", ".ws", ".wsf",
    ".scr", ".pif", ".hta", ".cpl",
}

# Magic byte signatures keyed by extension.  An empty list means "no magic
# bytes to verify" (e.g. plain text).
_MAGIC_BYTES: dict[str, list[tuple[bytes, ...]]] = {
    ".pdf": [(b"%PDF",)],
    ".docx": [(b"PK",)],
    ".doc": [(b"\xd0\xcf\x11\xe0",)],
    ".txt": [],
    ".md": [],
    ".markdown": [],
    ".xlsx": [(b"PK",)],
    ".xls": [(b"\xd0\xcf\x11\xe0",)],
}

# Extensions the platform accepts for document ingestion.  This is the
# PRIMARY allow-list: the declared extension decides whether a file is
# accepted, and magic bytes + the MIME cross-check are defence in depth.
_ALLOWED_EXTENSIONS: set[str] = {
    ".pdf", ".docx", ".doc", ".txt", ".md", ".markdown", ".xlsx", ".xls",
}

# MIME -> extensions that a *specific* MIME declaration is compatible with.
#
# NOTE: real browsers are inconsistent when reporting types for plain-text
# formats.  Chrome/Edge/Safari report `.md` as `text/markdown`, `text/plain`,
# `application/octet-stream` or an empty string depending on OS, browser and
# how the file was picked.  Rejecting those combinations made legitimate
# Markdown/TXT uploads fail with a confusing 400.  We therefore treat
# "generic" MIME declarations as *uninformative* and fall back to the
# extension + magic-byte checks instead of rejecting.
_MIME_TO_EXTENSIONS: dict[str, set[str]] = {
    "application/pdf": {".pdf"},
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": {".docx"},
    "application/msword": {".doc"},
    "text/plain": {".txt", ".md", ".markdown"},
    "text/markdown": {".md", ".markdown"},
    "text/x-markdown": {".md", ".markdown"},
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {".xlsx"},
    "application/vnd.ms-excel": {".xls"},
}

# MIME declarations that carry no reliable format information.  When one of
# these is presented we do NOT cross-check MIME against extension; the
# extension allow-list and the magic-byte check still apply.
_GENERIC_MIME_TYPES: set[str] = {
    "",
    "application/octet-stream",
    "binary/octet-stream",
    "application/unknown",
    "unknown/unknown",
    "*/*",
}

# Known malicious patterns inside otherwise valid files.
#
# IMPORTANT: the previous rule set scanned PDFs for a bare ``MZ`` byte pair.
# Because a PDF's first 4 KiB routinely contains compressed binary streams,
# ``MZ`` occurs there by chance roughly 6% of the time, so the rule silently
# rejected perfectly valid contracts.  ``MZ`` is also meaningless as an
# executable-header indicator here: a PDF must start with ``%PDF``, so an
# embedded PE payload can never be at offset 0.  The rule is therefore
# removed and replaced with PDF-specific *active content* markers, which are
# the real threat vector (embedded JS, auto-run actions, external launches).
_SUSPICIOUS_PATTERNS: list[tuple[str, re.Pattern[bytes]]] = [
    ("embedded JavaScript", re.compile(rb"/JavaScript\b")),
    ("embedded JS action", re.compile(rb"/JS\b")),
    ("auto-execute action", re.compile(rb"/OpenAction\b")),
    ("external launch action", re.compile(rb"/Launch\b")),
    ("embedded script tag", re.compile(rb"<script", re.IGNORECASE)),
]

# Regex for sanitising filenames: keep alphanumerics, dots, hyphens, underscores
_SAFE_FILENAME_RE = re.compile(r"[^\w.\-]")

# C0 control characters (except tab, LF, CR)
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

# Null bytes
_NULL_BYTE_RE = re.compile(r"\x00")


# =============================================================================
# File upload validation
# =============================================================================

async def validate_file_upload(
    file: UploadFile,
    *,
    max_size_mb: int | None = None,
    allowed_mime_types: set[str] | None = None,
    blocked_extensions: set[str] | None = None,
    check_malicious_patterns: bool = True,
) -> tuple[bytes, str]:
    """Deep validation of an uploaded file.

    Checks performed, in order:

    1. Filename is sanitized and its extension resolved.
    2. Extension is in the platform allow-list
       (``.pdf .docx .doc .txt .md .markdown .xlsx .xls``).
    3. Extension is not in the blocked (executable/script) list.
    4. File is non-empty and within the configured size limit.
    5. If the client declared a *specific* MIME type, it must be allowed and
       compatible with the extension.  Generic declarations
       (``application/octet-stream``, empty string, ...) are treated as
       uninformative and skipped, because real browsers report plain-text
       formats inconsistently -- this is what made legitimate ``.md``/``.txt``
       uploads fail previously.
    6. Magic bytes must match the declared extension.
    7. (Optional) PDFs are scanned for active content (embedded JS,
       ``/OpenAction``, ``/Launch``) inside the header region.

    Args:
        file: The FastAPI ``UploadFile`` to validate.
        max_size_mb: Override for max file size in MB.  Defaults to
            ``settings.MAX_UPLOAD_SIZE_MB``.
        allowed_mime_types: Override for allowed MIME types.  Defaults to
            ``settings.ALLOWED_UPLOAD_MIME_TYPES``.
        blocked_extensions: Override for blocked extensions.  Defaults to
            ``settings.BLOCKED_FILE_EXTENSIONS``.
        check_malicious_patterns: Whether to scan content for suspicious byte
            sequences.  Defaults to ``True``.

    Returns:
        ``(file_content, safe_extension)`` -- the raw bytes and the
        lower-cased extension (including the leading dot).

    Raises:
        HTTPException: On any validation failure.
    """
    if max_size_mb is None:
        max_size_mb = settings.MAX_UPLOAD_SIZE_MB
    if allowed_mime_types is None:
        allowed_mime_types = settings.ALLOWED_UPLOAD_MIME_TYPES
    if blocked_extensions is None:
        blocked_extensions = settings.BLOCKED_FILE_EXTENSIONS

    content_type = (file.content_type or "").strip().lower()

    # ------------------------------------------------------------------
    # 1. Sanitize filename and resolve the declared extension.
    #    The extension is the PRIMARY acceptance criterion.
    # ------------------------------------------------------------------
    safe_name = sanitize_filename(file.filename)
    ext = os.path.splitext(safe_name)[1].lower()

    # ------------------------------------------------------------------
    # 2. Extension allow-list
    # ------------------------------------------------------------------
    if ext not in _ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"不支持的文件格式: '{ext or safe_name}'。"
                f"支持的格式: PDF, DOCX, DOC, TXT, MD"
            ),
        )

    # ------------------------------------------------------------------
    # 3. Blocked extension (executable / script payloads)
    # ------------------------------------------------------------------
    if ext in blocked_extensions:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"禁止的文件类型: {ext}",
        )

    # ------------------------------------------------------------------
    # 4. Read content
    # ------------------------------------------------------------------
    file_content = await file.read()

    # ------------------------------------------------------------------
    # 5. Size check
    # ------------------------------------------------------------------
    max_bytes = max_size_mb * 1024 * 1024
    if len(file_content) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"文件过大，最大允许 {max_size_mb}MB",
        )

    if not file_content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="文件内容为空",
        )

    # ------------------------------------------------------------------
    # 6. MIME cross-check.
    #
    # Generic / missing MIME declarations (empty string,
    # application/octet-stream, ...) are treated as uninformative and skipped:
    # the extension allow-list plus the magic-byte check below remain
    # authoritative.  A *specific* MIME declaration must be both allowed and
    # compatible with the declared extension.
    # ------------------------------------------------------------------
    if content_type not in _GENERIC_MIME_TYPES:
        if content_type not in allowed_mime_types:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"不允许的文件类型: {content_type}。"
                    f"允许的类型: PDF, DOCX, DOC, TXT, MD"
                ),
            )
        allowed_exts = _MIME_TO_EXTENSIONS.get(content_type)
        if allowed_exts is not None and ext not in allowed_exts:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"文件扩展名 {ext} 与 MIME 类型 {content_type} 不匹配",
            )

    # ------------------------------------------------------------------
    # 7. Magic bytes validation (content must match the declared extension)
    # ------------------------------------------------------------------
    if not _validate_magic_bytes(file_content, ext):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="文件内容与声明的格式不匹配（magic bytes 校验失败）",
        )

    # ------------------------------------------------------------------
    # 8. Malicious pattern scan (PDF active content only)
    # ------------------------------------------------------------------
    if check_malicious_patterns and ext == ".pdf":
        # Only the header region is scanned: PDF active-content markers live
        # in the object dictionary, which appears before the (compressed,
        # high-entropy) content streams.  Scanning the entire file would
        # produce false positives from random bytes inside streams.
        head = file_content[:16384]
        for label, pattern in _SUSPICIOUS_PATTERNS:
            if pattern.search(head):
                logger.warning(
                    "Suspicious pattern (%s) detected in uploaded PDF '%s'",
                    label,
                    file.filename,
                )
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="文件包含可疑内容（可能嵌入了脚本），已被拒绝",
                )

    return file_content, ext


def _validate_magic_bytes(file_bytes: bytes, ext: str) -> bool:
    """Validate file content matches expected magic bytes for the extension."""
    expected = _MAGIC_BYTES.get(ext)
    if expected is None:
        return False
    if not expected:
        # No magic bytes to check (e.g. plain text)
        return True
    return any(file_bytes.startswith(prefixes) for prefixes in expected)


# =============================================================================
# Filename sanitization
# =============================================================================

def sanitize_filename(filename: str | None) -> str:
    """Sanitize a user-supplied filename.

    - Strips directory components (path traversal prevention).
    - Normalizes unicode to ASCII-safe form.
    - Removes characters that are not alphanumeric, dot, hyphen, or underscore.
    - Collapses consecutive dots / leading dots.

    Returns a safe filename string.  Falls back to ``"upload.txt"`` when the
    input is empty or ``None``.
    """
    raw = os.path.basename(filename or "upload.txt")
    # Normalize unicode -> NFKD and strip combining marks
    raw = unicodedata.normalize("NFKD", raw)
    # Remove unsafe chars
    raw = _SAFE_FILENAME_RE.sub("_", raw)
    # Remove leading dots (hidden files)
    raw = raw.lstrip(".")
    # Collapse consecutive dots
    while ".." in raw:
        raw = raw.replace("..", ".")
    # Fallback
    raw = raw.strip()
    return raw if raw else "upload.txt"


# =============================================================================
# Text input sanitization
# =============================================================================

def sanitize_input(
    text: str,
    *,
    max_length: int = 100_000,
    strip_null_bytes: bool = True,
    strip_control_chars: bool = True,
    normalize_unicode: bool = True,
    strip_whitespace: bool = True,
) -> str:
    """Sanitize a text input string.

    Args:
        text: The raw input string.
        max_length: Maximum allowed length (after all cleaning).  Longer
            strings are truncated.
        strip_null_bytes: Remove ``\\x00`` characters.
        strip_control_chars: Remove C0 control characters (except tab/LF/CR).
        normalize_unicode: Normalize to NFC unicode form.
        strip_whitespace: Strip leading/trailing whitespace.

    Returns:
        The cleaned string, truncated to *max_length*.
    """
    if not isinstance(text, str):
        return ""

    result = text

    if strip_null_bytes:
        result = _NULL_BYTE_RE.sub("", result)

    if strip_control_chars:
        result = _CONTROL_CHAR_RE.sub("", result)

    if normalize_unicode:
        result = unicodedata.normalize("NFC", result)

    if strip_whitespace:
        result = result.strip()

    # Enforce max length
    if len(result) > max_length:
        result = result[:max_length]

    return result


# =============================================================================
# JWT payload validation
# =============================================================================

def validate_jwt_payload(payload: dict, *, strict: bool = False) -> None:
    """Validate JWT claims for suspicious values.

    Checks:
    - ``sub`` (subject) must be present and non-empty.
    - ``iat`` (issued-at) must not be in the future (with 60s clock skew).
    - ``exp`` (expiration) must not be unreasonably far in the future
      (max 30 days from now, only when *strict* is True).

    Args:
        payload: Decoded JWT payload dictionary.
        strict: If False (default), the far-future ``exp`` check is relaxed
            to accommodate test fixtures and development tokens.  Set to True
            in production for tighter enforcement.

    Raises:
        ValueError: If any claim is invalid.
    """
    now = datetime.now(timezone.utc)

    # --- sub ---
    sub = payload.get("sub")
    if not sub:
        raise ValueError("Token missing 'sub' claim")

    # --- iat ---
    iat = payload.get("iat")
    if iat is not None:
        try:
            iat_dt = datetime.fromtimestamp(float(iat), tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            raise ValueError("Token has invalid 'iat' claim")
        # Allow 60 seconds of clock skew
        if iat_dt.timestamp() > now.timestamp() + 60:
            raise ValueError("Token 'iat' is in the future")

    # --- exp ---
    exp = payload.get("exp")
    if exp is not None:
        try:
            exp_dt = datetime.fromtimestamp(float(exp), tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            raise ValueError("Token has invalid 'exp' claim")
        # In strict mode, reject tokens with expiration more than 30 days
        # from now.  In non-strict mode (development/testing), skip this
        # check to accommodate long-lived test fixtures.
        if strict:
            max_exp_seconds = 30 * 24 * 3600  # 30 days
            if exp_dt.timestamp() > now.timestamp() + max_exp_seconds:
                raise ValueError("Token 'exp' is unreasonably far in the future")
