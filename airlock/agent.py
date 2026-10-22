from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Protocol

from .config import Config
from .edits import FENCE, EditError, parse_edits, strip_edits
from .llm import Completion, LLMClient
from .repo_tools import AccessDenied, RepoTools

INTENT_PROMPT = """You route messages for a coding assistant that can see a small code repository.

Message: "{task}"

Is the user asking to find or fix a bug, or to change code (FIX), or only asking a question or chatting
(QUESTION)? Treat "what is wrong with X", complaints about wrong results, and "fix X" as FIX.
Reply with exactly one word: FIX or QUESTION."""

HEADER = """You are a careful Python engineer helping in an offline repository. You can see its files below.

User request: {task}

Repository files:
{listing}

{context}
{tests}{focus}"""

FIX_PROMPT = HEADER + """
Reply in exactly this format:
1. ONE short plain-English sentence (max 25 words) for a non-programmer: quote the faulty code exactly
   as written, say what it does wrong, and what it should do instead.
2. For each file you change: a line `FILE: <path>`, then one or more edit blocks like this:
<<<<<<< SEARCH
the exact lines to replace, copied from the file with their indentation
=======
the new lines
>>>>>>> REPLACE

Fix only the problem the user asked about and leave unrelated bugs alone. The cause may be in a
different file than the one the user mentions; if fixing it needs changes in several files (for example
a renamed name and its callers), change each of them. The tests show the intended behaviour, so make
the code match them. Change as few lines as possible and never change test files."""

QUESTION_PROMPT = HEADER + """
Answer the user in at most 3 short plain-English sentences, based only on the files above.
Do not change or rewrite any code, and do not include code blocks."""

FOCUS_NOTE = "\nThe user mentions {target}; the cause may be there or in a file it uses.\n"
TESTS_SECTION = "\n--- current test output (failing) ---\n{output}\n"
MAX_TEST_CHARS = 2500

SELF_CHECK_NOTE = """

You already proposed this change (it was NOT applied; the files above are unchanged):
{diff}
With that change the tests still fail:
{output}
{broken}Reply again in the same format with a complete fix. SEARCH blocks must match the files shown above."""
ECHOED_INSTRUCTIONS = re.compile(r"\b(ONE short plain-English sentence|For each file you change|Reply in exactly "
                                 r"this format|Reply again in the same format)\b.*", re.IGNORECASE | re.DOTALL)

RETRY_NOTE = ("\n\nYour previous reply was rejected: {error}. Reply again in the same format: "
              "the sentence, then FILE: lines with SEARCH/REPLACE blocks.")
MAX_EXPLANATION_CHARS = 400
MAX_ANSWER_CHARS = 1200


class ProposalError(Exception):
    pass


@dataclass(frozen=True)
class Context:
    target: str | None  # file the request points at, if any
    files: dict[str, str]


@dataclass
class Proposal:
    """Either a fix (path -> (original, updated)) or a plain answer."""
    changes: dict[str, tuple[str, str]]
    context_files: list[str]
    explanation: str = ""
    answer: str = ""
    completions: list[Completion] = field(default_factory=list)

    @property
    def targets(self) -> list[str]:
        return list(self.changes)


class ContextSelector(Protocol):
    def select(self, task: str, tools: RepoTools) -> Context: ...


def is_test_file(path: str) -> bool:
    name = PurePosixPath(path).name
    return name.startswith("test_") or name.endswith("_test.py")


class RepoContextSelector:
    """All readable files within the budget; the file the task points at (and its tests) go first."""

    def select(self, task: str, tools: RepoTools) -> Context:
        files = tools.list_files()
        target = self._mentioned(task, files) or self._keyword_match(task, tools, files)
        order = list(files)
        if target:
            stem = PurePosixPath(target).stem
            related = [f for f in files if PurePosixPath(f).name in (f"test_{stem}.py", f"{stem}_test.py")]
            order = [target, *related, *[f for f in files if f != target and f not in related]]
        budget = tools.limits.max_context_chars
        picked: dict[str, str] = {}
        for rel in order:
            try:
                text = tools.read_file(rel)
            except AccessDenied:
                continue
            if len(text) > budget:
                continue
            picked[rel] = text
            budget -= len(text)
        return Context(target, picked)

    @staticmethod
    def _mentioned(task: str, files: list[str]) -> str | None:
        words = set(re.findall(r"[\w./-]+\.\w+", task))
        hits = [f for f in files if f in words or PurePosixPath(f).name in words]
        sources = [f for f in hits if not is_test_file(f)]
        return (sources or hits or [None])[0]

    @staticmethod
    def _keyword_match(task: str, tools: RepoTools, files: list[str]) -> str | None:
        scores: dict[str, int] = {}
        for word in set(re.findall(r"[A-Za-z_]{4,}", task)):
            for hit in tools.search(word):
                if hit.path.endswith(".py") and not is_test_file(hit.path):
                    scores[hit.path] = scores.get(hit.path, 0) + 1
        return max(scores, key=scores.get) if scores else None


def extract_explanation(text: str) -> str:
    prose = " ".join(ECHOED_INSTRUCTIONS.sub("", strip_edits(text)).split())
    prose = re.sub(r"\s+`?[\w./-]+\.\w+`?:$", "", prose)  # dangling "bill.py:" before the edit blocks
    prose = re.sub(r"(^|\s)[123AB][.)]\s+", r"\1", prose)
    prose = re.sub(r"\s*[123][.)]?\s*$", "", prose)
    return prose[:MAX_EXPLANATION_CHARS]


def validate(changes: dict[str, tuple[str, str]], max_files: int) -> None:
    if not changes:
        raise ProposalError("model proposed no change")
    if len(changes) > max_files:
        raise ProposalError(f"a fix may change at most {max_files} files (got {len(changes)})")
    for path, (_, updated) in changes.items():
        if is_test_file(path):
            raise ProposalError(f"test files must not be changed ({path})")
        if path.endswith(".py"):
            try:
                compile(updated, path, "exec")
            except SyntaxError as e:
                raise ProposalError(f"proposed code in {path} has a syntax error: {e}")


BROKEN_NOTE = ("Your change broke these tests, which passed before it: {tests}. Do not change the names "
               "or behaviour they rely on; fix the code that uses them instead.\n")


def self_check_feedback(diff: str, test_output: str, broken: set[str] = frozenset()) -> str:
    note = BROKEN_NOTE.format(tests=", ".join(sorted(broken))) if broken else ""
    return SELF_CHECK_NOTE.format(diff=diff, output=test_output[-MAX_TEST_CHARS:], broken=note)


class Agent:
    def __init__(self, llm: LLMClient, tools: RepoTools, config: Config,
                 selector: ContextSelector | None = None):
        self.llm = llm
        self.tools = tools
        self.config = config
        self.selector = selector or RepoContextSelector()

    def select_context(self, task: str) -> Context:
        return self.selector.select(task, self.tools)

    def classify(self, task: str) -> str:
        """'fix' or 'question'; anything unclear is a question, so nothing gets changed."""
        reply = self.llm.complete(INTENT_PROMPT.format(task=task)).text
        words = re.findall(r"[A-Za-z]+", reply.upper())
        return "fix" if words and words[0] == "FIX" else "question"

    def build_prompt(self, template: str, task: str, ctx: Context, test_output: str = "") -> str:
        listing = "\n".join(f"- {f}" for f in self.tools.list_files())
        context = "\n\n".join(f"--- {p} ---\n{t}" for p, t in ctx.files.items())
        tests = TESTS_SECTION.format(output=test_output[-MAX_TEST_CHARS:]) if test_output else ""
        focus = FOCUS_NOTE.format(target=ctx.target) if ctx.target else ""
        return template.format(task=task, listing=listing, context=context, tests=tests, focus=focus)

    def _changes(self, text: str, ctx: Context) -> dict[str, tuple[str, str]]:
        try:
            edits = parse_edits(text, ctx.files, ctx.target)
        except EditError as e:
            raise ProposalError(str(e))
        changes = {p: (ctx.files[p], new) for p, new in edits.items() if new != ctx.files[p]}
        validate(changes, self.config.max_files_per_fix)
        return changes

    def answer(self, task: str, ctx: Context | None = None) -> Proposal:
        ctx = ctx or self.select_context(task)
        completion = self.llm.complete(self.build_prompt(QUESTION_PROMPT, task, ctx))
        text = " ".join(FENCE.sub(" ", completion.text).split())[:MAX_ANSWER_CHARS]
        return Proposal({}, list(ctx.files), answer=text or "(no answer)", completions=[completion])

    def propose(self, task: str, ctx: Context | None = None, test_output: str = "", feedback: str = "") -> Proposal:
        ctx = ctx or self.select_context(task)
        prompt = self.build_prompt(FIX_PROMPT, task, ctx, test_output) + feedback
        completions: list[Completion] = []
        last_error: ProposalError | None = None
        for _ in range(self.config.max_attempts):
            retry = RETRY_NOTE.format(error=last_error) if last_error else ""
            completion = self.llm.complete(prompt + retry)
            completions.append(completion)
            try:
                changes = self._changes(completion.text, ctx)
            except ProposalError as e:
                last_error = e
                continue
            return Proposal(changes, list(ctx.files), extract_explanation(completion.text), completions=completions)
        raise ProposalError(f"{last_error} (after {len(completions)} attempts)")
