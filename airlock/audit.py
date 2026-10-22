from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REDACTIONS = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
     "[REDACTED PRIVATE KEY]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED AWS KEY]"),
    (re.compile(r"\b(?:sk|ghp|gho|xox[abp])[-_][A-Za-z0-9_-]{16,}\b"), "[REDACTED TOKEN]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]+=*"), "Bearer [REDACTED]"),
    (re.compile(r"(?i)\b([\w-]*(?:api[_-]?key|token|secret|password|passwd)[\w-]*)([ \t]*[:=][ \t]*)"
                r"(['\"]?)[^\s'\"]+\3"), r"\1\2\3[REDACTED]\3"),
    (re.compile(r"/(?:home|Users)/[^/\s]+"), "~"),
]


def redact(value: Any) -> Any:
    if isinstance(value, str):
        for pattern, repl in REDACTIONS:
            value = pattern.sub(repl, value)
        return value
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class AuditRun:
    """Collects one run's events; written once as a redacted JSON line."""

    def __init__(self, path: Path, **fields: Any):
        self.path = path
        self.started = time.monotonic()
        self.record: dict[str, Any] = {"started_at": utc_now(), **fields}

    def set(self, **fields: Any) -> None:
        self.record.update(fields)

    def finish(self, outcome: str) -> dict[str, Any]:
        self.record.update(outcome=outcome, finished_at=utc_now(),
                           total_seconds=round(time.monotonic() - self.started, 1))
        clean = redact(self.record)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(clean, ensure_ascii=False) + "\n")
        return clean


class AuditLog:
    def __init__(self, directory: Path):
        self.path = directory / "audit.jsonl"

    def start(self, **fields: Any) -> AuditRun:
        return AuditRun(self.path, **fields)
