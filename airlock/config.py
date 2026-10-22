from __future__ import annotations

from dataclasses import dataclass, field

IGNORED_DIRS = frozenset({".git", ".hg", ".svn", "venv", ".venv", "env", "__pycache__",
                          "node_modules", ".pytest_cache", ".mypy_cache", ".tox"})
SECRET_NAMES = frozenset({".env", ".netrc", ".pypirc", "id_rsa", "id_ed25519", "credentials"})
SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".crt", ".env")
SECRET_WORDS = ("secret", "credential", "password")


@dataclass(frozen=True)
class Limits:
    max_file_bytes: int = 100_000
    max_files: int = 500
    max_search_hits: int = 50
    max_context_chars: int = 12_000


@dataclass(frozen=True)
class ModelConfig:
    name: str = "qwen3.5:4b"
    url: str = "http://127.0.0.1:11434"
    temperature: float = 0.0
    num_ctx: int = 8192
    num_predict: int = 1500
    timeout_s: int = 300
    keep_alive: str = "30m"


@dataclass(frozen=True)
class Config:
    limits: Limits = field(default_factory=Limits)
    model: ModelConfig = field(default_factory=ModelConfig)
    max_attempts: int = 2
    max_files_per_fix: int = 3
    self_checks: int = 1  # extra model rounds when the proposed fix fails the tests
