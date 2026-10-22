from __future__ import annotations

import difflib
import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .repo_tools import AccessDenied, RepoTools

NO_EOL = "\\ No newline at end of file\n"
GIT_TIMEOUT_S = 30


class PatchError(Exception):
    pass


def build_diff(path: str, original: str, updated: str) -> str:
    """Unified diff (git-style a/ b/ paths) from original to updated."""
    lines = difflib.unified_diff(original.splitlines(keepends=True), updated.splitlines(keepends=True),
                                 f"a/{path}", f"b/{path}")
    return "".join(l if l.endswith("\n") else l + "\n" + NO_EOL for l in lines)


def diff_stats(diff: str) -> tuple[int, int]:
    lines = diff.splitlines()
    added = sum(1 for l in lines if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in lines if l.startswith("-") and not l.startswith("---"))
    return added, removed


def build_patch(changes: dict[str, tuple[str, str]]) -> str:
    return "".join(build_diff(path, original, updated) for path, (original, updated) in changes.items())


def validate_patch(diff: str, targets: list[str], tools: RepoTools, workspace: GitWorkspace) -> None:
    """Git must see the patch touching exactly the target files, and each must resolve inside the repo."""
    if not diff.startswith("--- a/"):
        raise PatchError("not a unified diff")
    touched = workspace.touched(diff)
    if sorted(touched) != sorted(set(targets)) or len(touched) != len(targets):
        raise PatchError(f"patch touches unexpected files: {touched}")
    for target in targets:
        try:
            tools.resolve(target)
        except AccessDenied as e:
            raise PatchError(str(e))


class GitWorkspace:
    """Git-backed checkpoint and patch application for the repo root (no shell)."""

    def __init__(self, root: Path):
        self.root = root

    def _git(self, *args: str, stdin: str | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        try:
            return subprocess.run(["git", *args], cwd=cwd or self.root, input=stdin, capture_output=True,
                                  text=True, timeout=GIT_TIMEOUT_S, env=env)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise PatchError(f"git {args[0]} failed: {e}")

    def require_clean(self) -> str:
        """Return HEAD if root is a git toplevel with a clean tree, else raise."""
        top = self._git("rev-parse", "--show-toplevel")
        if top.returncode != 0:
            raise PatchError("repo is not a git repository; init and commit first")
        if Path(top.stdout.strip()).resolve() != self.root:
            raise PatchError("repo path must be the git top-level directory")
        status = self._git("status", "--porcelain")
        if status.returncode != 0 or status.stdout.strip():
            raise PatchError("working tree is not clean; commit or stash changes first")
        head = self._git("rev-parse", "--short", "HEAD")
        if head.returncode != 0:
            raise PatchError("repo has no commits; commit a checkpoint first")
        return head.stdout.strip()

    def touched(self, diff: str) -> list[str]:
        """Paths git would modify, straight from git's own parser."""
        r = self._git("apply", "--numstat", "-", stdin=diff)
        if r.returncode != 0:
            raise PatchError(f"patch is malformed: {r.stderr.strip()}")
        return [line.split("\t", 2)[2] for line in r.stdout.splitlines() if line.count("\t") >= 2]

    def check(self, diff: str) -> None:
        r = self._git("apply", "--check", "-", stdin=diff)
        if r.returncode != 0:
            raise PatchError(f"patch does not apply cleanly: {r.stderr.strip()}")

    @contextmanager
    def scratch(self, diff: str) -> Iterator[Path]:
        """A throwaway copy of the repo (without .git) with the patch applied, for testing before approval."""
        with tempfile.TemporaryDirectory(prefix="airlock-check-") as tmp:
            copy = Path(tmp) / self.root.name
            shutil.copytree(self.root, copy, symlinks=True, ignore=shutil.ignore_patterns(".git"))
            r = self._git("apply", "-", stdin=diff, cwd=copy)
            if r.returncode != 0:
                raise PatchError(f"patch does not apply to the scratch copy: {r.stderr.strip()}")
            yield copy

    def apply(self, diff: str) -> None:
        self.check(diff)
        r = self._git("apply", "-", stdin=diff)
        if r.returncode != 0:
            raise PatchError(f"git apply failed: {r.stderr.strip()}")
