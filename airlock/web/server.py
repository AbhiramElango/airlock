from __future__ import annotations

import argparse
import json
import socket
import subprocess
import threading
import time
from dataclasses import asdict, replace
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..audit import AuditLog
from ..cli import DEFAULT_AUDIT_DIR
from ..config import Config, ModelConfig
from ..llm import OllamaClient
from ..repo_tools import AccessDenied, RepoTools
from ..session import Answer, Pending, Session, SessionError
from ..verify import CommandRunner, RunResult, TestRunner
from .terminal import DemoTerminal

STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_FILES = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"),
                "/style.css": ("style.css", "text/css")}
MAX_BODY = 10_000
PROBE = ("1.1.1.1", 443)
PROBE_TTL_S = 10
RESET_TIMEOUT_S = 30


def result_dict(r: RunResult | None) -> dict | None:
    return None if r is None else {**asdict(r), "summary": r.summary, "seconds": round(r.seconds, 1)}


class NetworkProbe:
    """Cached 'can this machine reach the internet?' check for the status badge."""

    def __init__(self):
        self._checked, self._online = 0.0, False

    def online(self) -> bool:
        if time.monotonic() - self._checked > PROBE_TTL_S:
            try:
                socket.create_connection(PROBE, timeout=1.5).close()
                self._online = True
            except OSError:
                self._online = False
            self._checked = time.monotonic()
        return self._online


def mem_available_mb() -> int | None:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except OSError:
        pass
    return None


class AgentUI:
    """One job at a time: ask -> pending -> approve/reject. Thread-safe state for polling."""

    def __init__(self, session: Session, tools: RepoTools, commands: CommandRunner, terminal: DemoTerminal,
                 reset_script: Path | None = None):
        self.session, self.tools, self.commands, self.terminal = session, tools, commands, terminal
        self.reset_script = reset_script
        self.lock = threading.Lock()
        self.pending: Pending | None = None
        self.job: dict = {"id": 0, "status": "idle"}
        self.model: dict = {"name": session.config.model.name, "loading": False, "error": None}
        session.emit = self._on_event

    def _on_event(self, event: str, data: dict) -> None:
        if event == "thinking":  # shown as a live indicator by the page
            return
        text = {
            "context": lambda: f"Reading {len(data.get('files', {}))} files",
            "intent": lambda: ("Understood as a fix request" if data["intent"] == "fix"
                               else "Understood as a question (no code changes)"),
            "tests_before": lambda: f"Tests before: {data['result'].status} ({data['result'].summary})",
            "attempts": lambda: "Model answered in " + ", ".join(f"{a['seconds']}s" for a in data["attempts"]),
            "self_check": lambda: f"Self-check: {data['note']}",
            "applied": lambda: f"Applied to {', '.join(data['targets'])}",
        }.get(event, lambda: event)()
        with self.lock:
            self.job.setdefault("steps", []).append({"t": round(time.time() - self.job["started"], 1), "text": text})

    def snapshot(self) -> dict:
        with self.lock:
            return json.loads(json.dumps(self.job))

    def ask(self, task: str) -> dict:
        task = task.strip()
        if not task:
            raise ValueError("type a question first")
        with self.lock:
            if self.job["status"] in ("working", "applying"):
                raise ValueError("the agent is busy")
            if self.model["loading"]:
                raise ValueError(f"{self.model['name']} is still loading")
            if self.pending:
                self.session.reject(self.pending, outcome="abandoned")
                self.pending = None
            self.job = {"id": self.job["id"] + 1, "status": "working", "task": task,
                        "started": time.time(), "steps": []}
        threading.Thread(target=self._propose, args=(task,), daemon=True).start()
        return self.snapshot()

    def _propose(self, task: str) -> None:
        try:
            pending = self.session.propose(task)
        except SessionError as e:
            with self.lock:
                self.job.update(status="error", outcome=e.outcome, error=e.message)
            return
        if isinstance(pending, Answer):
            with self.lock:
                self.job.update(status="answered", answer=pending.text)
            return
        with self.lock:
            self.pending = pending
            self.job.update(status="pending", targets=pending.targets, explanation=pending.explanation,
                            diff=pending.diff, tests_before=result_dict(pending.tests_before),
                            self_check=result_dict(pending.self_check), self_check_ok=pending.self_check_ok)

    def decide(self, approve: bool) -> dict:
        with self.lock:
            pending, self.pending = self.pending, None
            if not pending:
                raise ValueError("nothing to approve")
            self.job["status"] = "applying" if approve else "rejected"
        if not approve:
            self.session.reject(pending)
            return self.snapshot()
        try:
            after = self.session.apply(pending)
        except SessionError as e:
            with self.lock:
                self.job.update(status="error", outcome=e.outcome, error=e.message)
            return self.snapshot()
        with self.lock:
            self.job.update(status="done", tests_after=result_dict(after))
        return self.snapshot()

    def models(self) -> dict:
        try:
            available = self.session.llm.names()
        except OSError:
            available = []
        with self.lock:
            return {**self.model, "available": available or [self.model["name"]]}

    def set_model(self, name: str) -> dict:
        """Switch to another installed model: unload the old one, load the new one in the background."""
        if name not in self.session.llm.names():
            raise ValueError(f"model is not installed: {name}")
        with self.lock:
            if self.job["status"] in ("working", "applying") or self.model["loading"]:
                raise ValueError("the agent is busy")
            if self.pending:
                raise ValueError("approve or reject the proposed change first")
            if name != self.model["name"]:
                old, new = self.session.llm, self.session.llm.with_model(name)
                self.session.use_model(new, name)
                self.model = {"name": name, "loading": True, "error": None}
                threading.Thread(target=self._load_model, args=(old, new), daemon=True).start()
        return self.models()

    def _load_model(self, old, new) -> None:
        error = None
        try:
            old.unload()  # one model in RAM at a time
            new.warm_up()
        except OSError as e:
            error = f"could not load {self.model['name']}: {e}"
        with self.lock:
            self.model.update(loading=False, error=error)

    def reset(self) -> dict:
        """Restore the demo repo with the configured reset script (fixed command, no shell)."""
        if not self.reset_script:
            raise ValueError("reset is not enabled for this repo")
        with self.lock:
            if self.job["status"] in ("working", "applying"):
                raise ValueError("the agent is busy")
            pending, self.pending = self.pending, None
            self.job = {"id": self.job["id"] + 1, "status": "idle"}
        if pending:
            self.session.reject(pending, outcome="abandoned")
        r = subprocess.run([str(self.reset_script)], cwd=self.reset_script.parent, capture_output=True,
                           text=True, timeout=RESET_TIMEOUT_S, stdin=subprocess.DEVNULL)
        if r.returncode != 0:
            raise ValueError(f"reset failed: {(r.stderr or r.stdout).strip()[-300:]}")
        return {"reset": True}

    def run_script(self, rel: str) -> dict:
        if not rel.endswith(".py") or rel not in self.tools.list_files():
            raise ValueError("can only run a Python file listed in this repo")
        self.tools.resolve(rel)
        result = self.commands.run(self.tools.root, [self.commands.python, rel])
        return {"path": rel, "result": result_dict(result)}


class Handler(BaseHTTPRequestHandler):
    ui: AgentUI
    probe: NetworkProbe
    allowed_hosts: set[str]

    def log_message(self, fmt, *args):  # keep the terminal quiet
        pass

    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; connect-src 'self'")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, status: int = 200) -> None:
        self._send(status, json.dumps(data).encode(), "application/json")

    def _error(self, status: int, message: str) -> None:
        self._json({"error": message}, status)

    def _host_ok(self) -> bool:
        if self.headers.get("Host", "") in self.allowed_hosts:
            return True
        self._error(HTTPStatus.FORBIDDEN, "bad host")
        return False

    def do_GET(self):
        if not self._host_ok():
            return
        url = urlparse(self.path)
        if url.path in STATIC_FILES:
            name, ctype = STATIC_FILES[url.path]
            return self._send(200, (STATIC_DIR / name).read_bytes(), ctype)
        if url.path == "/api/status":
            return self._json({"online": self.probe.online(), "model": self.ui.model["name"],
                               "mem_available_mb": mem_available_mb(), "can_reset": bool(self.ui.reset_script)})
        if url.path == "/api/files":
            return self._json({"files": self.ui.tools.list_files()})
        if url.path == "/api/file":
            rel = parse_qs(url.query).get("path", [""])[0]
            try:
                return self._json({"path": rel, "text": self.ui.tools.read_file(rel)})
            except AccessDenied as e:
                return self._error(HTTPStatus.FORBIDDEN, str(e))
        if url.path == "/api/models":
            return self._json(self.ui.models())
        if url.path == "/api/job":
            return self._json(self.ui.snapshot())
        self._error(HTTPStatus.NOT_FOUND, "not found")

    def do_POST(self):
        if not self._host_ok():
            return
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            return self._error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "json only")
        length = int(self.headers.get("Content-Length", "0"))
        if length > MAX_BODY:
            return self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "too large")
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            routes = {
                "/api/ask": lambda: self.ui.ask(str(body.get("task", ""))),
                "/api/approve": lambda: self.ui.decide(True),
                "/api/reject": lambda: self.ui.decide(False),
                "/api/run": lambda: self.ui.run_script(str(body.get("path", ""))),
                "/api/reset": self.ui.reset,
                "/api/model": lambda: self.ui.set_model(str(body.get("name", ""))),
                "/api/term": lambda: self.ui.terminal.run(str(body.get("cmd", ""))[:500]),
            }
            if self.path not in routes:
                return self._error(HTTPStatus.NOT_FOUND, "not found")
            return self._json(routes[self.path]())
        except (ValueError, AccessDenied) as e:
            return self._error(HTTPStatus.CONFLICT, str(e))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="airlock-ui", description="Local web UI for the offline coding agent.")
    p.add_argument("--repo", required=True)
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--bind", default="127.0.0.1", help="listen address (0.0.0.0 only inside the isolated container)")
    p.add_argument("--allow-host", action="append", default=[], help="extra accepted Host header, e.g. 172.30.0.10:8080")
    p.add_argument("--model", default=ModelConfig.name)
    p.add_argument("--reset-script", type=Path, help="enables the Reset demo button (e.g. scripts/reset_demo.sh)")
    args = p.parse_args(argv)

    config = Config(model=replace(ModelConfig(), name=args.model))
    tools = RepoTools(args.repo, config.limits)
    tests = TestRunner()
    session = Session(tools.root, config, OllamaClient(config.model), tests, AuditLog(DEFAULT_AUDIT_DIR))
    commands = CommandRunner(timeout_s=10)
    reset = args.reset_script.resolve(strict=True) if args.reset_script else None
    Handler.ui = AgentUI(session, tools, commands, DemoTerminal(tools, commands, tests), reset)
    Handler.probe = NetworkProbe()
    Handler.allowed_hosts = {f"127.0.0.1:{args.port}", f"localhost:{args.port}", *args.allow_host}
    server = ThreadingHTTPServer((args.bind, args.port), Handler)
    server.daemon_threads = True
    print(f"UI on http://{args.bind}:{args.port}  (repo {tools.root}, model {config.model.name}); Ctrl-C to stop", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
