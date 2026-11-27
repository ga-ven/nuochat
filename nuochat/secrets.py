import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path


class _Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.c_void_p)]


def _secret_path() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return root / "NuoChat" / "secret.bin"


def _protect(data: bytes, decrypt=False):
    if os.name != "nt":
        raise RuntimeError("DPAPI 密钥存储仅支持 Windows")
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt.CryptProtectData.argtypes = [
        ctypes.POINTER(_Blob), wintypes.LPCWSTR, ctypes.POINTER(_Blob),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob),
    ]
    crypt.CryptProtectData.restype = wintypes.BOOL
    crypt.CryptUnprotectData.argtypes = crypt.CryptProtectData.argtypes
    crypt.CryptUnprotectData.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p

    buffer = ctypes.create_string_buffer(data)
    source = _Blob(len(data), ctypes.cast(buffer, ctypes.c_void_p))
    entropy = ctypes.create_string_buffer(b"NuoChat/v1")
    optional = _Blob(len(entropy.raw), ctypes.cast(entropy, ctypes.c_void_p))
    result = _Blob()
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    ok = function(ctypes.byref(source), None, ctypes.byref(optional), None, None, 1, ctypes.byref(result))
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        if result.data:
            kernel.LocalFree(result.data)


class SecretStore:
    def __init__(self, path=None):
        self.path = Path(path) if path else _secret_path()

    def load_all(self) -> dict:
        try:
            raw = _protect(self.path.read_bytes(), decrypt=True).decode("utf-8")
        except FileNotFoundError:
            return {}
        raw = raw.strip()
        if not raw:
            return {}
        if raw.startswith("{"):
            try:
                data = json.loads(raw)
            except ValueError:
                return {}
            if isinstance(data, dict):
                return {str(key): value for key, value in data.items()
                        if isinstance(value, str) and value}
            return {}
        return {"primary": raw}

    def save_all(self, keys):
        clean = {str(key): value.strip() for key, value in dict(keys or {}).items()
                 if isinstance(value, str) and value.strip()}
        if not clean:
            self.clear()
            return
        encrypted = _protect(json.dumps(clean, ensure_ascii=False).encode("utf-8"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(encrypted)

    def load(self) -> str:
        return self.load_all().get("primary", "")

    def save(self, value: str):
        values = self.load_all()
        if value:
            values["primary"] = value
        else:
            values.pop("primary", None)
        self.save_all(values)

    def clear(self):
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
