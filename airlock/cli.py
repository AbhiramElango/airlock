from __future__ import annotations

import argparse
import sys
import textwrap
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from .audit import AuditLog
from .config import Config, ModelConfig
from .llm import LLMClient, OllamaClient
from .patcher import diff_stats
from .session import Answer, Session, SessionError
from .verify import RunResult, TestRunner

COLORS = {"+": "\033[32m", "-": "\033[31m", "@": "\033[36m"}
RESET = "\033[0m"

Approver = Callable[[str], bool]

EXIT_OK, EXIT_REJECTED, EXIT_SETUP, EXIT_OLLAMA, EXIT_PROPOSAL, EXIT_PATCH, EXIT_TESTS = range(7)
DEFAULT_AUDIT_DIR = Path(__file__).resolve().parent.parent / "audit"


def console_approver(prompt: str) -> bool:
    try:
        return input(prompt).strip().lower() in ("y", "yes")
    except EOFError:
        return False


def colorize(diff: str, enabled: bool) -> str:
    if not enabled:
        return diff
    out = []
    for line in diff.splitlines():
        color = "" if line.startswith(("+++", "---")) else COLORS.get(line[:1], "")
        out.append(f"{color}{line}{RESET}" if color else line)
    return "\n".join(out) + "\n"


def section(title: str) -> None:
    print(f"\n== {title} ==")


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="airlock", description="Offline coding agent (local Ollama).")
    p.add_argument("task", nargs="*", help="e.g. what is wrong with bill.py? (asked interactively if omitted)")
    p.add_argument("--repo", help="git repo root; all file access stays inside it (required)")
    p.add_argument("--warm-up", action="store_true", help="only load the model into memory, then exit")
    p.add_argument("--model", default=ModelConfig.name, help="Ollama model tag")
    p.add_argument("--test-timeout", type=int, default=60, help="seconds before pytest is killed")
    p.add_argument("--audit-dir", default=str(DEFAULT_AUDIT_DIR), help="where audit.jsonl is written")
    p.add_argument("--no-color", action="store_true")
    return p.parse_args(argv)


EXIT_CODES = {"rejected": EXIT_REJECTED, "setup_error": EXIT_SETUP, "ollama_unreachable": EXIT_OLLAMA,
              "no_valid_proposal": EXIT_PROPOSAL, "model_timeout": EXIT_OLLAMA, "invalid_patch": EXIT_PATCH, "apply_failed": EXIT_PATCH,
              "applied_tests_failed": EXIT_TESTS, "applied_tests_passed": EXIT_OK, "answered": EXIT_OK}


@dataclass
class Deps:
    llm: LLMClient
    approve: Approver
    runner: TestRunner
    audit: AuditLog


def print_tests(label: str, result: RunResult, show_output: bool) -> None:
    print(f"{label}: {result.status.upper()} in {result.seconds:.1f}s ({result.summary})")
    if show_output:
        print(result.output.rstrip())


def console_listener(event: str, data: dict) -> None:
    if event == "context":
        section("Context")
        print(f"repo:       {data['root']} (checkpoint {data['checkpoint']})")
        print(f"focus:      {data['target'] or '(whole repo)'}")
        for rel, lines in data["files"].items():
            print(f"  - {rel} ({lines} lines)")
    elif event == "tests_before":
        section("Tests before")
        print_tests("baseline", data["result"], show_output=False)
    elif event == "thinking":
        section(f"Model {data['model']}")
        print("thinking...", flush=True)
    elif event == "intent":
        print(f"understood as: {'fix request' if data['intent'] == 'fix' else 'question (no code changes)'}")
    elif event == "attempts":
        for i, a in enumerate(data["attempts"], 1):
            print(f"attempt {i}: {a['seconds']:.1f}s, {a['tokens']} tokens")
    elif event == "self_check":
        print(f"self-check: {data['note']}")
    elif event == "applied":
        files = " ".join(data["targets"])
        print(f"applied to {', '.join(data['targets'])}; undo with: git -C {data['root']} checkout -- {files}")


def run(args: argparse.Namespace, config: Config, deps: Deps) -> int:
    session = Session(args.repo, config, deps.llm, deps.runner, deps.audit, console_listener)
    outcome = flow(args, session, deps.approve)
    print(f"\naudit: {outcome} -> {deps.audit.path}")
    return EXIT_CODES[outcome]


def flow(args: argparse.Namespace, session: Session, approve: Approver) -> str:
    try:
        pending = session.propose(args.task)
        if isinstance(pending, Answer):
            section("Answer")
            print(textwrap.fill(pending.text, width=100))
            return "answered"
        if pending.explanation:
            section("What is wrong")
            print(textwrap.fill(pending.explanation, width=100))
        added, removed = diff_stats(pending.diff)
        section(f"Proposed diff (+{added} -{removed})")
        print(colorize(pending.diff, sys.stdout.isatty() and not args.no_color), end="")
        if not pending.self_check_ok:
            print("\nwarning: the tests still fail with this change")
        section("Approval")
        if not approve(f"Apply this patch to {', '.join(pending.targets)}? [y/N] "):
            session.reject(pending)
            print("rejected; no files were changed")
            return "rejected"
        after = session.apply(pending)
    except SessionError as e:
        if e.message:
            print(f"error: {e.message}", file=sys.stderr)
        return e.outcome
    section("Tests after")
    print_tests("result", after, show_output=not after.passed)
    return "applied_tests_passed" if after.passed else "applied_tests_failed"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    args.task = " ".join(args.task)
    config = Config(model=replace(ModelConfig(), name=args.model))
    try:
        llm = OllamaClient(config.model)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_SETUP
    if args.warm_up:
        try:
            llm.warm_up()
        except OSError as e:
            print(f"error: cannot reach local Ollama at {config.model.url}: {e}", file=sys.stderr)
            return EXIT_OLLAMA
        return EXIT_OK
    if not args.repo:
        print("error: --repo is required", file=sys.stderr)
        return EXIT_SETUP
    if not args.task:
        try:
            args.task = input("What should I do? ").strip()
        except EOFError:
            args.task = ""
        if not args.task:
            print("error: no task given", file=sys.stderr)
            return EXIT_SETUP
    deps = Deps(llm, console_approver, TestRunner(args.test_timeout), AuditLog(Path(args.audit_dir)))
    return run(args, config, deps)


if __name__ == "__main__":
    sys.exit(main())
