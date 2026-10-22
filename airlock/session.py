from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable
from urllib.error import URLError

from .agent import Agent, Context, ProposalError, self_check_feedback
from .audit import AuditLog, AuditRun
from .config import Config
from .llm import LLMClient
from .patcher import GitWorkspace, PatchError, build_patch, validate_patch
from .repo_tools import AccessDenied, RepoTools
from .verify import RunResult, TestRunner, check_note, failures, improves

Listener = Callable[[str, dict], None]


class SessionError(Exception):
    def __init__(self, outcome: str, message: str = ""):
        super().__init__(message)
        self.outcome, self.message = outcome, message


@dataclass
class Answer:
    """The model answered a question; nothing to apply."""
    task: str
    text: str


@dataclass
class Pending:
    """A validated proposal waiting for a human decision."""
    task: str
    targets: list[str]
    explanation: str
    diff: str
    tests_before: RunResult
    audit: AuditRun = field(repr=False)
    workspace: GitWorkspace = field(repr=False)
    self_check: RunResult | None = None  # tests run on a scratch copy with the change applied
    self_check_ok: bool = True
    self_check_note: str = ""


def test_record(result: RunResult) -> dict:
    return {"status": result.status, "returncode": result.returncode,
            "seconds": round(result.seconds, 1), "summary": result.summary, "output_tail": result.output}


class Session:
    """Propose → approve/reject → verify for one repo. Shared by the CLI and the web UI."""

    def __init__(self, repo: str | Path, config: Config, llm: LLMClient, runner: TestRunner,
                 audit_log: AuditLog, listener: Listener | None = None):
        self.repo = Path(repo)
        self.config = config
        self.llm = llm
        self.runner = runner
        self.audit_log = audit_log
        self.emit = listener or (lambda event, data: None)

    def use_model(self, llm: LLMClient, name: str) -> None:
        self.llm = llm
        self.config = replace(self.config, model=replace(self.config.model, name=name))

    def _fail(self, audit: AuditRun, outcome: str, message: str = "") -> SessionError:
        audit.set(error=message or None)
        audit.finish(outcome)
        return SessionError(outcome, message)

    def propose(self, task: str) -> Pending | Answer:
        audit = self.audit_log.start(task=task, repo=str(self.repo.resolve()), model=self.config.model.name,
                                     test_command=" ".join(["python", *self.runner.command[1:]]))
        try:
            tools = RepoTools(self.repo, self.config.limits)
            workspace = GitWorkspace(tools.root)
            checkpoint = workspace.require_clean()
            agent = Agent(self.llm, tools, self.config)
            ctx = agent.select_context(task)
        except (OSError, ValueError, AccessDenied, ProposalError, PatchError) as e:
            raise self._fail(audit, "setup_error", str(e))
        audit.set(checkpoint=checkpoint, focus=ctx.target, context_files=list(ctx.files))
        self.emit("context", {"root": str(tools.root), "checkpoint": checkpoint, "target": ctx.target,
                              "files": {p: len(t.splitlines()) for p, t in ctx.files.items()}})

        self.emit("thinking", {"model": self.config.model.name})
        try:
            intent = self._model(agent.classify, task)
            audit.set(intent=intent)
            self.emit("intent", {"intent": intent})
            if intent == "question":
                proposal = self._model(agent.answer, task, ctx)
                audit.set(attempts=self._attempts(proposal.completions), answer=proposal.answer)
                self.emit("attempts", {"attempts": self._attempts(proposal.completions)})
                audit.finish("answered")
                return Answer(task, proposal.answer)
            before = self.runner.run(tools.root)
            audit.set(tests_before=test_record(before))
            self.emit("tests_before", {"result": before})
            return self._fix(task, agent, ctx, before, tools, workspace, audit)
        except SessionError as e:
            raise self._fail(audit, e.outcome, e.message)

    def _fix(self, task: str, agent: Agent, ctx: Context, before: RunResult, tools: RepoTools,
             workspace: GitWorkspace, audit: AuditRun) -> Pending:
        """Propose a fix, test it on a scratch copy, and give the model another round if the tests fail."""
        failing = "" if before.passed else before.output
        best, feedback, completions, checks = None, "", [], []
        for _ in range(1 + self.config.self_checks):
            try:
                proposal = self._model(agent.propose, task, ctx, failing, feedback)
                completions += proposal.completions
                diff = build_patch(proposal.changes)
                try:
                    validate_patch(diff, proposal.targets, tools, workspace)
                    workspace.check(diff)
                except PatchError as e:
                    raise SessionError("invalid_patch", str(e))
            except SessionError:
                if best is None:
                    raise
                break  # keep the earlier valid proposal
            self.emit("attempts", {"attempts": self._attempts(completions)})
            check = self._check(workspace, diff)
            ok, note = improves(before, check), check_note(before, check)
            checks.append({**test_record(check), "ok": ok, "note": note})
            self.emit("self_check", {"result": check, "ok": ok, "note": note})
            candidate = (proposal, diff, check, ok, note)
            if best is None or self._rank(candidate) < self._rank(best):
                best = candidate
            if ok:
                break
            feedback = self_check_feedback(diff, check.output, failures(check) - failures(before))
        proposal, diff, check, ok, note = best
        audit.set(attempts=self._attempts(completions), self_checks=checks, targets=proposal.targets,
                  explanation=proposal.explanation, diff=diff)
        return Pending(task, proposal.targets, proposal.explanation, diff, before, audit, workspace,
                       check, ok, note)

    def _model(self, step: Callable, *args):
        """Run one model step; transport and parsing problems become SessionErrors."""
        try:
            return step(*args)
        except URLError as e:
            raise SessionError("ollama_unreachable", f"cannot reach local Ollama at {self.config.model.url}: {e.reason}")
        except TimeoutError:
            raise SessionError("model_timeout", f"the model took longer than {self.config.model.timeout_s}s")
        except ProposalError as e:
            raise SessionError("no_valid_proposal", str(e))

    def _check(self, workspace: GitWorkspace, diff: str) -> RunResult:
        try:
            with workspace.scratch(diff) as copy:
                return self.runner.run(copy)
        except (PatchError, OSError) as e:
            return RunResult("error", None, 0.0, str(e))

    @staticmethod
    def _rank(candidate) -> tuple[bool, int]:
        """Lower is better: changes that pass the self-check first, then fewer failing tests."""
        _, _, check, ok, _ = candidate
        return (not ok, 0 if check.passed else len(failures(check)) or 10**6)

    @staticmethod
    def _attempts(completions) -> list[dict]:
        return [{"seconds": round(c.seconds, 1), "tokens": c.tokens} for c in completions]

    def reject(self, pending: Pending, outcome: str = "rejected") -> None:
        pending.audit.set(approved=False)
        pending.audit.finish(outcome)

    def apply(self, pending: Pending) -> RunResult:
        pending.audit.set(approved=True)
        try:
            pending.workspace.apply(pending.diff)
        except PatchError as e:
            raise self._fail(pending.audit, "apply_failed", str(e))
        self.emit("applied", {"targets": pending.targets, "root": str(pending.workspace.root)})
        after = self.runner.run(pending.workspace.root)
        pending.audit.set(tests_after=test_record(after))
        pending.audit.finish("applied_tests_passed" if after.passed else "applied_tests_failed")
        return after
