from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path


class ResponseCache:
    def __init__(self, directory: str | Path, ttl_s: int, enabled: bool = True):
        self.directory = Path(directory)
        self.ttl_s = ttl_s
        self.enabled = enabled
        self._lock = threading.Lock()

    @staticmethod
    def make_key(provider: str, model: str, system: str, user: str) -> str:
        digest = hashlib.sha256()
        for part in (provider, model, system, user):
            digest.update(part.encode("utf-8"))
            digest.update(b"\x00")
        return digest.hexdigest()

    def _path_for(self, key: str) -> Path:
        return self.directory / key[:2] / f"{key}.json"

    def get(self, key: str):
        if not self.enabled:
            return None
        path = self._path_for(key)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(raw, dict) or "stored_at" not in raw:
            return None
        if self.ttl_s > 0 and time.time() - raw["stored_at"] > self.ttl_s:
            return None
        return raw.get("value")

    def set(self, key: str, value) -> None:
        if not self.enabled or value is None:
            return
        path = self._path_for(key)
        payload = json.dumps({"stored_at": time.time(), "value": value}, ensure_ascii=False)
        try:
            with self._lock:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(".tmp")
                tmp.write_text(payload, encoding="utf-8")
                tmp.replace(path)
        except OSError:
            return
