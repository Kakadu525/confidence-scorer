from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FunctionChange:
    file_path: str
    qualname: str
    language: str
    change_type: str
    old_source: str | None
    new_source: str | None
    is_public: bool
