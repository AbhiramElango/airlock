"""Turn a model reply into file edits: SEARCH/REPLACE blocks (preferred) or one full file (fallback)."""
from __future__ import annotations

import re
from typing import Protocol

FILE_LINE = re.compile(r"^\s*(?:\d[.)]\s*)?\**FILE:?\**\s*`?([\w./-]+)`?", re.MULTILINE | re.IGNORECASE)
SEARCH = re.compile(r"^\s*<{5,9}\s*SEARCH\b")
DIVIDER = re.compile(r"^\s*={5,9}\s*$")
REPLACE = re.compile(r"^\s*>{5,9}\s*REPLACE\b")
FENCE_LINE = re.compile(r"^\s*```")
FENCE = re.compile(r"```[a-zA-Z0-9_+-]*\n(.*?)```", re.DOTALL)
BLOCK = re.compile(r"<{5,9}\s*SEARCH\b.*?(>{5,9}\s*REPLACE\b|\Z)", re.DOTALL)


class EditError(Exception):
    pass


class EditFormat(Protocol):
    def parse(self, text: str, files: dict[str, str], default: str | None) -> dict[str, str] | None:
        """path -> updated text, or None when the reply is not in this format."""


def _unfence(lines: list[str]) -> list[str]:
    while lines and FENCE_LINE.match(lines[0]):
        lines = lines[1:]
    while lines and FENCE_LINE.match(lines[-1]):
        lines = lines[:-1]
    return lines


def _indent(line: str) -> str:
    return line[:len(line) - len(line.lstrip())]


def apply_block(content: str, search: list[str], replace: list[str], path: str) -> str:
    """Replace the one place where `search` occurs; tolerant of trailing spaces and uniform indent shifts."""
    if not any(l.strip() for l in search):
        raise EditError(f"empty SEARCH block for {path}")
    lines, n = content.splitlines(), len(search)

    def find(norm):
        want = [norm(l) for l in search]
        return [i for i in range(len(lines) - n + 1) if [norm(l) for l in lines[i:i + n]] == want]

    hits, shifted = find(str.rstrip), False
    if not hits:
        hits, shifted = find(str.strip), True
    if not hits:
        raise EditError(f"SEARCH text not found in {path}; copy the lines exactly as they are in the file")
    if len(hits) > 1:
        raise EditError(f"SEARCH text matches {len(hits)} places in {path}; include more surrounding lines")
    i = hits[0]
    if shifted:  # model got the indentation wrong; re-base its replacement on the file's indentation
        have, got = _indent(lines[i]), _indent(search[0])
        replace = [have + l[len(got):] if l.startswith(got) else l for l in replace]
    out = lines[:i] + replace + lines[i + n:]
    return "\n".join(out) + ("\n" if content.endswith("\n") or not content else "")


class SearchReplace:
    def parse(self, text, files, default):
        blocks = self._blocks(text, default)
        if not blocks:
            return None
        updated: dict[str, str] = {}
        for path, search, replace in blocks:
            if path not in files:
                raise EditError(f"the FILE: line must name one of the repository files (got {path!r})")
            updated[path] = apply_block(updated.get(path, files[path]), search, replace, path)
        return updated

    @staticmethod
    def _blocks(text: str, default: str | None) -> list[tuple[str | None, list[str], list[str]]]:
        path, blocks, state = default, [], None
        search: list[str] = []
        replace: list[str] = []
        for line in text.splitlines():
            if state is None:
                if m := FILE_LINE.match(line):
                    path = m.group(1)
                elif SEARCH.match(line):
                    state, search, replace = "search", [], []
            elif state == "search":
                if DIVIDER.match(line):
                    state = "replace"
                else:
                    search.append(line)
            elif REPLACE.match(line):
                blocks.append((path, _unfence(search), _unfence(replace)))
                state = None
            else:
                replace.append(line)
        if state is not None:
            raise EditError("an edit block is not closed with >>>>>>> REPLACE")
        return blocks


class FullFile:
    def parse(self, text, files, default):
        blocks = FENCE.findall(text)
        if not blocks:
            return None
        m = FILE_LINE.search(FENCE.sub("", text))
        path = m.group(1) if m else default
        if not path or path not in files:
            raise EditError(f"the FILE: line must name one of the repository files (got {path!r})")
        return {path: max(blocks, key=len).strip("\n") + "\n"}


FORMATS: tuple[EditFormat, ...] = (SearchReplace(), FullFile())


def parse_edits(text: str, files: dict[str, str], default: str | None,
                formats: tuple[EditFormat, ...] = FORMATS) -> dict[str, str]:
    for fmt in formats:
        edits = fmt.parse(text, files, default)
        if edits is not None:
            return edits
    raise EditError("reply has no SEARCH/REPLACE edit blocks")


def strip_edits(text: str) -> str:
    """The reply without edit blocks, code blocks and FILE: lines (what is left is the explanation)."""
    text = FENCE.sub(" ", BLOCK.sub(" ", text))
    return "\n".join(l for l in text.splitlines() if not FILE_LINE.match(l) and not FENCE_LINE.match(l))
