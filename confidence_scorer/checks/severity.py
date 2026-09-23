from __future__ import annotations

SEVERITY_CEILINGS = {"high": 40, "medium": 75}


def severity_ceiling(items: list[dict]) -> int | None:
    ceilings = [
        SEVERITY_CEILINGS[severity]
        for item in items
        if isinstance(item, dict) and (severity := str(item.get("severity", "")).lower()) in SEVERITY_CEILINGS
    ]
    return min(ceilings) if ceilings else None


def worst_severity(items: list[dict]) -> str | None:
    present = {
        str(item.get("severity", "")).lower() for item in items if isinstance(item, dict)
    } & SEVERITY_CEILINGS.keys()
    return min(present, key=SEVERITY_CEILINGS.__getitem__) if present else None


def apply_ceiling(score: float, items: list[dict]) -> float:
    ceiling = severity_ceiling(items)
    return min(score, ceiling) if ceiling is not None else score
