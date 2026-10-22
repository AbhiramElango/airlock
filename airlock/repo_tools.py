from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .config import IGNORED_DIRS, SECRET_NAMES, SECRET_SUFFIXES, SECRET_WORDS, Limits


class AccessDenied(Exception):
    pass


@dataclass(frozen=True)
class SearchHit:
    path: str
    line_no: int
    line: str


def is_secret_name(name: str) -> bool:
    low = name.lower()
    return (low in SECRET_NAMES or low.endswith(SECRET_SUFFIXES)
            or any(w in low for w in SECRET_WORDS))


class RepoTools:
    """Read-only access confined to one project root."""

    def __init__(self, root: str | Path, limits: Limits = Limits()):
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError(f"not a directory: {self.root}")
        self.limits = limits

    def resolve(self, rel: str) -> Path:
        """Map a repo-relative path to a real path inside root, or raise AccessDenied."""
        p = PurePosixPath(rel.replace("\\", "/"))
        if p.is_absolute() or rel.startswith("~"):
            raise AccessDenied(f"absolute path not allowed: {rel}")
        if ".." in p.parts:
            raise AccessDenied(f"path traversal not allowed: {rel}")
        if any(part in IGNORED_DIRS for part in p.parts):
            raise AccessDenied(f"ignored directory: {rel}")
        if is_secret_name(p.name):
            raise AccessDenied(f"secret-like file: {rel}")
        real = (self.root / p).resolve()
        if real != self.root and self.root not in real.parents:
            raise AccessDenied(f"escapes repo root: {rel}")
        return real

    def rel(self, real: Path) -> str:
        return real.relative_to(self.root).as_posix()

    def _readable(self, real: Path) -> bool:
        try:
            if not real.is_file() or real.stat().st_size > self.limits.max_file_bytes:
                return False
            with open(real, "rb") as f:
                head = f.read(8192)
            return b"\0" not in head
        except OSError:
            return False

    def list_files(self) -> list[str]:
        out: list[str] = []
        for dirpath, dirnames, filenames in os.walk(self.root, followlinks=False):
            dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_DIRS)
            for name in sorted(filenames):
                rel = (Path(dirpath) / name).relative_to(self.root).as_posix()
                try:
                    real = self.resolve(rel)
                except AccessDenied:
                    continue
                if self._readable(real):
                    out.append(rel)
                    if len(out) >= self.limits.max_files:
                        return sorted(out)
        return sorted(out)

    def read_file(self, rel: str) -> str:
        real = self.resolve(rel)
        if not self._readable(real):
            raise AccessDenied(f"missing, too large, or binary: {rel}")
        try:
            return real.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raise AccessDenied(f"not utf-8 text: {rel}")

    def search(self, needle: str) -> list[SearchHit]:
        needle = needle.lower()
        hits: list[SearchHit] = []
        for rel in self.list_files():
            try:
                text = self.read_file(rel)
            except AccessDenied:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if needle in line.lower():
                    hits.append(SearchHit(rel, i, line.strip()[:200]))
                    if len(hits) >= self.limits.max_search_hits:
                        return hits
        return hits
