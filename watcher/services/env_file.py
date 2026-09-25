"""
Read and update the SMTP_PASSWORD line in backend/.env.

Only this one key is ever written. Everything else in .env (database
settings, SECRET_KEY, ENTRY_RULE, comments, blank lines) is preserved
byte-for-byte. This keeps the W-034 protection: the web can no longer
inject arbitrary lines into .env.

Rules:
  - Values containing line breaks or other control characters are
    rejected (that was the injection vector).
  - The value is always written inside double quotes. settings.py
    strips one pair of matching outer quotes, so the password is read
    back exactly as typed, including spaces, quotes, '#' and '='.
  - The file is written atomically (temp file + os.replace), so a crash
    can never leave a half-written .env.
  - The original file's permissions (and, where allowed, owner/group)
    are copied onto the new file (I-03). Without this, the temp file's
    private 0600 mode and the web process's owner would replace them,
    and a watcher/scheduler running as another user could lose access
    to .env after the next restart. A brand-new .env is created 0600.
  - Reads are done fresh from the file every time, so every process
    (web server, watcher subprocess, scheduler) uses the latest value
    without a restart.
"""

import os
import stat
import tempfile
from pathlib import Path

from django.conf import settings

SMTP_PASSWORD_KEY = "SMTP_PASSWORD"
MAX_SECRET_LENGTH = 256


class EnvFileError(Exception):
    """Raised when .env cannot be read or written."""


def env_file_path():
    """Path of the .env file (overridable in tests via SMTP_ENV_FILE)."""
    return Path(
        getattr(settings, "SMTP_ENV_FILE", None)
        or Path(settings.BASE_DIR) / ".env"
    )


def is_safe_env_value(value):
    """True if `value` can be written to .env without breaking lines."""
    if not isinstance(value, str) or len(value) > MAX_SECRET_LENGTH:
        return False
    return not any(ord(ch) < 32 or ord(ch) == 127 for ch in value)


def _line_key(raw_line):
    """Return the key of a KEY=value line (or "" for comments/blank)."""
    line = raw_line.strip()
    if not line or line.startswith("#"):
        return ""
    if line.lower().startswith("export "):
        line = line[len("export "):].strip()
    if "=" not in line:
        return ""
    return line.partition("=")[0].strip()


def _parse_value(raw_line):
    """Same parsing rules as _load_env_file() in settings.py."""
    line = raw_line.strip()
    if line.lower().startswith("export "):
        line = line[len("export "):].strip()
    value = line.partition("=")[2].strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1]
    return value


def read_env_value(key, path=None):
    """
    Return the value of `key` from .env, or None if the key is not in
    the file (or the file cannot be read).
    """
    path = path or env_file_path()
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return None

    value = None
    for raw_line in text.splitlines():
        if _line_key(raw_line) == key:
            value = _parse_value(raw_line)  # last occurrence wins
    return value

def _copy_file_identity(original, tmp_name):
    """
    I-03: give the temp file the original .env's mode (and owner/group
    where the OS allows it) before it replaces .env.

    On Windows, chmod only controls the read-only flag and chown does
    not exist, so this is effectively a no-op there; the permission
    problem it solves only arises on Linux/macOS servers.
    """
    if original is None:
        # New .env: private by default.
        os.chmod(tmp_name, 0o600)
        return

    os.chmod(tmp_name, stat.S_IMODE(original.st_mode))

    chown = getattr(os, "chown", None)  # absent on Windows
    if chown is None:
        return
    try:
        chown(tmp_name, original.st_uid, original.st_gid)
    except PermissionError:
        # Not root and not the owner: the mode is still preserved,
        # which covers the usual case of web and worker as one user.
        pass

def write_env_value(key, value, path=None):
    """
    Set `key` to `value` in .env (or remove it when value is None).

    Only lines for `key` are replaced; all other lines are kept as-is.
    Raises ValueError for unsafe values and EnvFileError on I/O errors.
    """
    if value is not None and not is_safe_env_value(value):
        raise ValueError("Value contains line breaks or control characters.")

    path = path or env_file_path()

    try:
        raw = path.read_bytes() if path.exists() else b""
    except OSError as exc:
        raise EnvFileError(f"Cannot read {path.name}: {exc}") from exc

    has_bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig") if raw else ""
    newline = "\r\n" if "\r\n" in text else "\n"

    kept = [line for line in text.splitlines() if _line_key(line) != key]

    if value is not None:
        # Always quoted: settings.py strips exactly one pair of outer quotes.
        kept.append(f'{key}="{value}"')

    new_text = newline.join(kept) + (newline if kept else "")
    data = new_text.encode("utf-8")
    if has_bom:
        data = b"\xef\xbb\xbf" + data

        # I-03: remember the existing file's identity so it survives the swap.
    try:
        original = os.stat(path)
    except FileNotFoundError:
        original = None
    except OSError as exc:
        raise EnvFileError(f"Cannot read {path.name}: {exc}") from exc

    directory = path.parent
    try:
        fd, tmp_name = tempfile.mkstemp(
            dir=str(directory), prefix=".env.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            _copy_file_identity(original, tmp_name)
            os.replace(tmp_name, str(path))
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
    except OSError as exc:
        raise EnvFileError(f"Cannot write {path.name}: {exc}") from exc

    # Keep this process in step with the file.
    if value is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = value