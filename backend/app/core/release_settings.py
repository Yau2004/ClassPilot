"""DPAPI-protected release settings with no dotenv/environment fallback."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from app.core.config import ReleaseSettings

_ENTROPY = b"ClassPilot release settings v1"
_CRYPTPROTECT_UI_FORBIDDEN = 0x1


class ReleaseSettingsDecryptionError(RuntimeError):
    """DPAPI could not decrypt settings for the current Windows user."""


class ReleaseSettingsFormatError(RuntimeError):
    """Protected settings are absent, malformed, or fail model validation."""


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes) -> tuple[_DataBlob, Any]:
    buffer = ctypes.create_string_buffer(data)
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def _crypt(data: bytes, *, protect: bool) -> bytes:
    if os.name != "nt":
        raise OSError("Release credentials require Windows DPAPI.")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    data_blob_pointer = ctypes.POINTER(_DataBlob)
    crypt32.CryptProtectData.argtypes = [
        data_blob_pointer, wintypes.LPCWSTR, data_blob_pointer, ctypes.c_void_p,
        ctypes.c_void_p, wintypes.DWORD, data_blob_pointer,
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [
        data_blob_pointer, ctypes.POINTER(wintypes.LPWSTR), data_blob_pointer,
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, data_blob_pointer,
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    source, source_buffer = _blob(data)
    entropy, entropy_buffer = _blob(_ENTROPY)
    output = _DataBlob()
    if protect:
        ok = crypt32.CryptProtectData(
            ctypes.byref(source),
            "ClassPilot release settings",
            ctypes.byref(entropy),
            None,
            None,
            _CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(output),
        )
    else:
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(source),
            None,
            ctypes.byref(entropy),
            None,
            None,
            _CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(output),
        )
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(output.pbData)
        del source_buffer, entropy_buffer


def protect_for_current_user(data: bytes) -> bytes:
    return _crypt(data, protect=True)


def unprotect_for_current_user(data: bytes) -> bytes:
    return _crypt(data, protect=False)


class ReleaseSettingsStore:
    """Read and atomically write settings encrypted for the current Windows user."""

    def __init__(self, settings_file: Path):
        self.settings_file = settings_file

    def save(self, settings: ReleaseSettings) -> None:
        payload = settings.model_dump(mode="json")
        payload["postgres_password"] = settings.postgres_password.get_secret_value()
        encrypted = protect_for_current_user(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
        self.settings_file.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(
            prefix=".settings-", suffix=".tmp", dir=self.settings_file.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(b"CPDP1" + encrypted)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.settings_file)
        finally:
            temporary.unlink(missing_ok=True)

    def load(self) -> ReleaseSettings:
        try:
            protected = self.settings_file.read_bytes()
        except OSError:
            raise ReleaseSettingsFormatError("Protected settings are unavailable.") from None
        if not protected.startswith(b"CPDP1"):
            raise ReleaseSettingsFormatError("Protected settings format is invalid.") from None
        try:
            cleartext = unprotect_for_current_user(protected[5:])
        except OSError:
            raise ReleaseSettingsDecryptionError("DPAPI decryption failed.") from None
        try:
            values = json.loads(cleartext.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            raise ReleaseSettingsFormatError("Protected settings content is invalid.") from None
        if not isinstance(values, dict):
            raise ReleaseSettingsFormatError("Protected settings content is invalid.") from None
        try:
            return ReleaseSettings.model_validate(values)
        except Exception:
            raise ReleaseSettingsFormatError("Protected settings do not match this release.") from None


def validate_release_settings(values: dict[str, object]) -> ReleaseSettings:
    """Validate explicit release values without consulting environment settings."""
    return ReleaseSettings.model_validate(values)
