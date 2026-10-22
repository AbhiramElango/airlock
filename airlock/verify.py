from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

PYTEST_NO_TESTS = 5
FAILURE_LINE = re.compile(r"^(?:FAILED|ERROR) (\S+)", re.MULTILINE)
ENV_KEEP = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")


@dataclass(frozen=True)
class RunResult:
    status: str  # passed | failed | timeout | no_tests | error
    returncode: int | None
    seconds: float
    output: str

    @property
    def passed(self) -> bool:
        return self.status == "passed"

    @property
    def summary(self) -> str:
        lines = [l for l in self.output.splitlines() if l.strip()]
        return lines[-1].strip("= ") if lines else self.status


def failures(result: RunResult) -> set[str]:
    """Failing test ids from pytest's short summary."""
    return set(FAILURE_LINE.findall(result.output))


def _interrupted(result: RunResult) -> bool:
    return "Interrupted:" in result.output  # collection error: the other tests never ran


def improves(before: RunResult, after: RunResult) -> bool:
    """True if `after` passes, or fixes failing tests without breaking any that passed."""
    if after.passed:
        return True
    if after.status != "failed" or _interrupted(after):
        return False
    if before.status != "failed" or _interrupted(before):
        return False  # tests did not run before, so nothing to compare against: only a full pass counts
    return failures(after) < failures(before)


def check_note(before: RunResult, after: RunResult) -> str:
    if after.passed:
        return "all tests pass with the change"
    if improves(before, after):
        fixed = len(failures(before) - failures(after))
        return f"fixes {fixed} failing test(s), breaks none; {len(failures(after))} still fail (other issues)"
    return f"tests still fail with the change ({after.summary})"


class FixedCommandRunner:
    """Runs an allowlisted command: no shell, scrubbed env, timeout, capped output."""

    statuses: dict[int, str] = {0: "passed"}

    def __init__(self, timeout_s: int = 60, max_output_chars: int = 4000, python: str = sys.executable):
        self.python = python
        self.timeout_s = timeout_s
        self.max_output_chars = max_output_chars

    def _execute(self, command: list[str], root: Path) -> RunResult:
        env = {k: os.environ[k] for k in ENV_KEEP if k in os.environ}
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        start = time.monotonic()
        try:
            p = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True,
                               timeout=self.timeout_s, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired as e:
            out = e.stdout.decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
            return RunResult("timeout", None, time.monotonic() - start,
                             self._tail(out + f"\n[timed out after {self.timeout_s}s]"))
        except OSError as e:
            return RunResult("error", None, time.monotonic() - start, str(e))
        status = self.statuses.get(p.returncode, "error")
        return RunResult(status, p.returncode, time.monotonic() - start, self._tail(p.stdout + p.stderr))

    def _tail(self, text: str) -> str:
        return text if len(text) <= self.max_output_chars else "...\n" + text[-self.max_output_chars:]


class TestRunner(FixedCommandRunner):
    """The predefined test suite; never a model-chosen command."""

    statuses = {0: "passed", 1: "failed", PYTEST_NO_TESTS: "no_tests"}

    @property
    def command(self) -> list[str]:
        return [self.python, "-m", "pytest", "-q", "-p", "no:cacheprovider"]

    def run(self, root: Path) -> RunResult:
        return self._execute(self.command, root)


class CommandRunner(FixedCommandRunner):
    """Runs an argv the caller already built and validated (UI Run button, demo terminal)."""

    statuses = {0: "ok"}

    def run(self, root: Path, argv: list[str]) -> RunResult:
        return self._execute(argv, root)
