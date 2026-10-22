import subprocess
from pathlib import Path

from airlock.llm import Completion
from airlock.verify import RunResult

ORIGINAL = "def chunk(xs, n):\n    return [xs[i:i + n] for i in range(0, len(xs) - n + 1, n)]\n"
FIXED = "def chunk(xs, n):\n    return [xs[i:i + n] for i in range(0, len(xs), n)]\n"


class FakeLLM:
    """Scripted replies; routing prompts get `intent` without using a reply."""

    def __init__(self, *replies, intent="FIX", installed=("fake",)):
        self.replies = list(replies)
        self.intent = intent
        self.prompts = []
        self.intent_prompts = []
        self.installed = list(installed)
        self.loads = []

    def names(self):
        return self.installed

    def with_model(self, name):
        self.loads.append(name)
        return self

    def unload(self):
        pass

    def warm_up(self):
        pass

    def complete(self, prompt):
        if prompt.startswith("You route messages"):
            self.intent_prompts.append(prompt)
            return Completion(self.intent, "fake", 0.0, 1)
        self.prompts.append(prompt)
        return Completion(self.replies.pop(0), "fake", 0.0, 0)


def fenced(code):
    return f"Here you go:\n```python\n{code}```\n"


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout


def make_git_repo(root: Path, files: dict) -> None:
    for rel, text in files.items():
        (root / rel).write_text(text)
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "-c", "user.name=t", "-c", "user.email=t@localhost", "commit", "-qm", "seed")


class FakeRunner:
    command = ["python", "-m", "pytest", "-q"]

    def __init__(self, after_status, checks=("passed",)):
        self.statuses = ["failed", *checks, after_status]  # before, self-check(s), after
        self.roots = []

    def run(self, root):
        self.roots.append(root)
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        summary = "1 failed, 5 passed" if status == "failed" else "6 passed"
        return RunResult(status, 0 if status == "passed" else 1, 0.1,
                          f"E   assert chunk([1, 2, 3]) == [[1, 2], [3]]\n{summary}")
