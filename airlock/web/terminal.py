from __future__ import annotations

import re
import shlex
from typing import Callable

from ..repo_tools import AccessDenied, RepoTools
from ..verify import CommandRunner, TestRunner

HOST = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.:-]{0,252}$")
URL = re.compile(r"^https?://[A-Za-z0-9][A-Za-z0-9.:-]*(/[\w./?=&%~+-]*)?$")

HELP = """Demo terminal (allowlisted commands only, no shell):
  ping <host>          e.g. ping 1.1.1.1
  curl <url>           e.g. curl https://google.com
  nslookup <host>      DNS lookup;  dig <host> also works
  ip addr | ip route   network interfaces / routes
  python <file.py>     run a repo file
  pytest               run the repo tests
  ls | cat <file>      list / show repo files
  clear | help"""


class Denied(Exception):
    pass


class DemoTerminal:
    """Maps a typed line to a fixed argv (validated) or a built-in; never runs a shell."""

    def __init__(self, tools: RepoTools, runner: CommandRunner, tests: TestRunner):
        self.tools, self.runner, self.tests = tools, runner, tests
        self.commands: dict[str, Callable[[list[str]], list[str] | str]] = {
            "help": lambda a: HELP,
            "ls": lambda a: "\n".join(self.tools.list_files()),
            "cat": lambda a: self.tools.read_file(self._one(a, "cat <file>")),
            "ping": lambda a: ["ping", "-c", "2", "-W", "2", self._host(a, "ping <host>")],
            "curl": lambda a: ["curl", "-sS", "-m", "5", "-o", "/dev/null", "-w", "HTTP %{http_code}\n",
                               self._url(a)],
            "nslookup": lambda a: ["nslookup", "-timeout=2", "-retry=1", self._host(a, "nslookup <host>")],
            "dig": lambda a: ["dig", "+time=2", "+tries=1", self._host(a, "dig <host>")],
            "ip": self._ip,
            "python": self._python,
            "python3": self._python,
            "pytest": lambda a: self.tests.command,
        }

    @staticmethod
    def _one(args: list[str], usage: str) -> str:
        if len(args) != 1 or args[0].startswith("-"):
            raise Denied(f"usage: {usage}")
        return args[0]

    def _host(self, args: list[str], usage: str) -> str:
        host = self._one(args, usage)
        if not HOST.match(host):
            raise Denied(f"not a valid host name: {host}")
        return host

    def _url(self, args: list[str]) -> str:
        url = self._one(args, "curl <url>")
        if not URL.match(url):
            raise Denied("usage: curl http(s)://host/path")
        return url

    @staticmethod
    def _ip(args: list[str]) -> list[str]:
        if args in (["addr"], ["a"]):
            return ["ip", "-br", "addr"]
        if args in (["route"], ["r"]):
            return ["ip", "route"]
        raise Denied("usage: ip addr | ip route")

    def _python(self, args: list[str]) -> list[str]:
        rel = self._one(args, "python <file.py>")
        if not rel.endswith(".py") or rel not in self.tools.list_files():
            raise Denied(f"can only run a .py file from this repo: {rel}")
        return [self.runner.python, rel]

    def run(self, line: str) -> dict:
        try:
            words = shlex.split(line)
        except ValueError as e:
            return {"status": "denied", "output": str(e)}
        if not words:
            return {"status": "ok", "output": ""}
        handler = self.commands.get(words[0])
        if handler is None:
            return {"status": "denied", "output": f"{words[0]}: not allowed in this demo terminal (type help)"}
        try:
            result = handler(words[1:])
        except (Denied, AccessDenied) as e:
            return {"status": "denied", "output": str(e)}
        if isinstance(result, str):
            return {"status": "ok", "output": result}
        r = self.runner.run(self.tools.root, result)
        return {"status": r.status, "output": r.output.rstrip() or f"(exit {r.returncode})"}
